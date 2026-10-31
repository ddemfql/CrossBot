"""Session drafts survive navigation and never overwrite a new server revision."""
import json
import unittest

from core.errors import ValidationError
from documents.drafts import DraftStore, NAMESPACE


class DraftStoreTests(unittest.TestCase):
    def setUp(self):
        self.state = {}
        self.store = DraftStore(self.state)
        self.scope = ("synthetic-case", "focused-review", "buyer")

    def load(self, revision=3, initial=None):
        return self.store.load(*self.scope, revision, {"value": "saved"} if initial is None else initial)

    def test_scalar_widget_capture_survives_unmount_and_preserves_server_snapshot(self):
        self.load()
        key = self.store.widget_key(*self.scope, "value")
        self.state[key] = "unsaved buyer"
        self.store.capture_widget(*self.scope, key, "value")
        del self.state[key]  # Streamlit removes a conditionally hidden widget.
        restored = DraftStore(self.state).load(*self.scope, 3, {"value": "saved"})
        self.assertEqual(restored.values, {"value": "unsaved buyer"})
        self.assertEqual(restored.base_values, {"value": "saved"})
        self.assertTrue(restored.dirty)
        self.assertTrue(restored.can_save)
        self.assertEqual(len(self.store.list_dirty("synthetic-case")), 1)

    def test_case_editor_and_target_are_isolated(self):
        self.load()
        self.store.update(*self.scope, {"value": "local draft"})
        for scope in (("another-case", self.scope[1], self.scope[2]),
                      (self.scope[0], "full-file", self.scope[2]),
                      (self.scope[0], self.scope[1], "seller")):
            self.assertEqual(self.store.load(*scope, 3, {"value": "other"}).values["value"], "other")
        self.assertEqual(self.load().values["value"], "local draft")

    def test_dirty_draft_keeps_original_revision_and_conflicts_on_new_server_data(self):
        self.load()
        self.store.update(*self.scope, {"value": "my unsaved edit"})
        conflict = self.load(4, {"value": "another human edit"})
        self.assertEqual(conflict.values["value"], "my unsaved edit")
        self.assertEqual(conflict.server_values["value"], "another human edit")
        self.assertEqual(conflict.base_revision, 3)
        self.assertEqual(conflict.current_revision, 4)
        self.assertTrue(conflict.stale)
        self.assertFalse(self.store.can_save(conflict))

    def test_clean_draft_refreshes_from_server_and_resets_widget_key(self):
        before = self.load()
        key = self.store.widget_key(*self.scope, "value")
        after = self.load(4, {"value": "updated"})
        self.assertFalse(before.dirty)
        self.assertFalse(after.stale)
        self.assertEqual(after.values["value"], "updated")
        self.assertNotEqual(key, self.store.widget_key(*self.scope, "value"))

    def test_discard_same_revision_resets_widget_epoch_and_ignores_old_callback(self):
        self.load()
        key = self.store.widget_key(*self.scope, "value")
        self.state[key] = "discarded draft"
        self.store.capture_widget(*self.scope, key, "value")
        reset = self.store.discard(*self.scope, 3, {"value": "saved"})
        self.assertFalse(reset.dirty)
        self.assertNotEqual(key, self.store.widget_key(*self.scope, "value"))
        self.store.capture_widget(*self.scope, key, "value")
        self.assertEqual(self.load().values["value"], "saved")

    def test_editor_captures_edit_delete_add_and_rotates_key_to_avoid_replay(self):
        base = [{"sku": "A", "quantity": "1", "included": False}, {"sku": "B", "quantity": "2"}]
        self.load(initial={"rows": base, "notes": "keep"})
        key = self.store.widget_key(*self.scope, "rows")
        self.state[key] = {"edited_rows": {"0": {"quantity": "9", "included": True}},
                           "deleted_rows": [1], "added_rows": [{"sku": "C", "quantity": "3"}]}
        captured = self.store.capture_editor(*self.scope, key, "rows", base)
        self.assertEqual(captured.values["rows"], [{"sku": "A", "quantity": "9", "included": True},
                                                  {"sku": "C", "quantity": "3"}])
        self.assertEqual(captured.values["notes"], "keep")
        self.assertEqual(base[0]["quantity"], "1")
        self.assertEqual(self.state[key]["deleted_rows"], [1])
        self.assertNotEqual(key, self.store.widget_key(*self.scope, "rows"))
        self.assertIsNone(self.store.capture_editor(*self.scope, key, "rows", captured.values["rows"]))
        new_key = self.store.widget_key(*self.scope, "rows")
        self.state[new_key] = {"edited_rows": {}, "deleted_rows": [], "added_rows": []}
        repeated = self.store.capture_editor(*self.scope, new_key, "rows", captured.values["rows"])
        self.assertEqual(repeated.values, captured.values)

    def test_values_are_detached_and_all_session_records_are_json_serializable(self):
        supplied = {"rows": [{"quantity": "2"}]}
        snapshot = self.load(initial=supplied)
        supplied["rows"][0]["quantity"] = "original mutated"
        snapshot.values["rows"][0]["quantity"] = "snapshot mutated"
        self.assertEqual(self.load(initial={"rows": [{"quantity": "2"}]}).values["rows"][0]["quantity"], "2")
        json.dumps(self.state, allow_nan=False)

    def test_invalid_edits_do_not_mutate_existing_draft(self):
        self.load()
        self.store.update(*self.scope, {"value": "keep draft"})
        before = json.dumps(self.state, sort_keys=True)
        with self.assertRaises(ValidationError):
            self.store.update(*self.scope, {"value": float("nan")})
        with self.assertRaises(ValidationError):
            self.store.discard(*self.scope, -1, {"value": "invalid revision"})
        self.assertEqual(json.dumps(self.state, sort_keys=True), before)

    def test_clear_after_explicit_save_removes_draft_and_ignores_late_widget_event(self):
        self.load()
        key = self.store.widget_key(*self.scope, "value")
        self.state[key] = "late unsaved event"
        self.store.clear(*self.scope)
        self.assertEqual(self.state[NAMESPACE], {})
        self.assertIsNone(self.store.capture_widget(*self.scope, key, "value"))
        self.assertEqual(self.store.list_dirty(), [])

    def test_noop_verification_is_saveable_and_boolean_zero_are_distinct(self):
        clean = self.load(initial={"value": False})
        self.assertTrue(clean.can_save)
        changed = self.store.update(*self.scope, {"value": 0})
        self.assertTrue(changed.dirty)

    def test_same_server_business_token_rebases_revision_noise_without_changing_draft(self):
        self.store.load(*self.scope, 3, {"value": "saved"}, concurrency_token="a" * 64)
        self.store.update(*self.scope, {"value": "my draft"})
        refreshed=self.store.load(*self.scope, 4, {"value": "saved"}, concurrency_token="a" * 64)
        self.assertTrue(refreshed.dirty)
        self.assertFalse(refreshed.stale)
        self.assertEqual(refreshed.base_revision, 4)
        self.assertEqual(refreshed.values["value"], "my draft")
        self.assertEqual(refreshed.base_values["value"], "saved")

    def test_different_or_missing_token_does_not_rebase_dirty_revision(self):
        for token in (None, "b" * 64):
            with self.subTest(token=token):
                state={};store=DraftStore(state)
                store.load(*self.scope, 3, {"value": "saved"}, concurrency_token="a" * 64)
                store.update(*self.scope, {"value": "my draft"})
                refreshed=store.load(*self.scope, 4, {"value": "saved"}, concurrency_token=token)
                self.assertTrue(refreshed.stale)
                self.assertEqual(refreshed.base_revision, 3)

    def test_equal_token_cannot_replace_changed_server_values_or_upgrade_unbound_draft(self):
        self.store.load(*self.scope, 3, {"value": "saved"}, concurrency_token="a" * 64)
        self.store.update(*self.scope, {"value": "my draft"})
        changed=self.store.load(*self.scope, 4, {"value": "different server value"}, concurrency_token="a" * 64)
        self.assertTrue(changed.stale)
        self.assertEqual(changed.base_values["value"], "saved")
        strict=DraftStore({})
        strict.load(*self.scope,3,{"value":"saved"})
        strict.update(*self.scope,{"value":"my draft"})
        self.assertTrue(strict.load(*self.scope,4,{"value":"saved"},concurrency_token="a"*64).stale)

    def test_changed_business_token_conflicts_even_without_a_revision_increment(self):
        self.store.load(*self.scope,3,{"value":"saved"},concurrency_token="a"*64)
        self.store.update(*self.scope,{"value":"draft"})
        self.assertTrue(self.store.load(*self.scope,3,{"value":"saved"},concurrency_token="b"*64).stale)

    def test_real_streamlit_callback_survives_switching_targets_without_autosave(self):
        from streamlit.testing.v1 import AppTest
        app = AppTest.from_string('''import streamlit as st
from documents.drafts import DraftStore
store=DraftStore(st.session_state)
target=st.selectbox("正在编辑",["buyer","seller"])
snapshot=store.load("synthetic-case","focused-review",target,1,{"value":"saved-"+target})
key=store.widget_key("synthetic-case","focused-review",target,"value")
st.text_input("本地草稿",snapshot.values["value"],key=key,on_change=store.capture_widget,
    args=("synthetic-case","focused-review",target,key,"value"))
st.write("此演示没有后台保存操作")
''', default_timeout=20)
        app.run()
        app.text_input[0].set_value("unsaved-buyer").run()
        app.selectbox[0].select("seller").run()
        self.assertEqual(app.text_input[0].value, "saved-seller")
        app.selectbox[0].select("buyer").run()
        self.assertFalse(app.exception, [e.message for e in app.exception])
        self.assertEqual(app.text_input[0].value, "unsaved-buyer")
        records=app.session_state[NAMESPACE]
        buyer=next(r for r in records.values() if r["scope"][2]=="buyer")
        self.assertEqual(buyer["base_values"]["value"], "saved-buyer")
        self.assertEqual(buyer["values"]["value"], "unsaved-buyer")


if __name__ == "__main__":
    unittest.main()
