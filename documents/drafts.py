"""Session-only editor drafts; persistence always requires an explicit save.

Use stable case/editor/target identities, not the current review digest. Widget
keys may disappear when Streamlit unmounts controls; these records do not. A
dirty draft remains attached to its original workspace revision and server
values until the user saves or explicitly discards it.
"""
from dataclasses import dataclass
from hashlib import sha256
import json

from core.errors import ValidationError

NAMESPACE = "_w3_unsaved_drafts_v1"
EPOCHS = "_w3_draft_widget_epochs_v1"


def _copy(value):
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValidationError("草稿包含暂不支持的内容，请检查填写值。") from exc


def _same(left, right):
    return json.dumps(left, sort_keys=True, ensure_ascii=False) == json.dumps(right, sort_keys=True, ensure_ascii=False)


@dataclass(frozen=True)
class DraftSnapshot:
    case_id: str
    editor_id: str
    target_id: str
    values: dict
    base_values: dict
    server_values: dict
    base_revision: int
    current_revision: int
    dirty: bool
    stale: bool
    key: str
    base_concurrency_token: str | None = None
    current_concurrency_token: str | None = None

    @property
    def can_save(self):
        return not self.stale


class DraftStore:
    def __init__(self, session_state):
        self.state = session_state

    @staticmethod
    def _key(case_id, editor_id, target_id):
        if any(not isinstance(v, str) or not v.strip() or len(v) > 2000
               for v in (case_id, editor_id, target_id)):
            raise ValidationError("草稿填写位置无效。")
        return sha256(json.dumps([case_id, editor_id, target_id], ensure_ascii=False).encode()).hexdigest()

    def _records(self):
        return _copy(self.state.get(NAMESPACE, {}))

    def _put(self, key, record):
        records = self._records()
        records[key] = _copy(record)
        self.state[NAMESPACE] = records
        return self._snapshot(key, record)

    def _epoch(self, key):
        epochs = _copy(self.state.get(EPOCHS, {}))
        epochs[key] = epochs.get(key, 0) + 1
        self.state[EPOCHS] = epochs
        return epochs[key]

    @staticmethod
    def _snapshot(key, record):
        dirty = not _same(record["values"], record["base_values"])
        base_token, token = record.get("base_concurrency_token"), record.get("current_concurrency_token")
        stale = dirty and (record["base_revision"] != record["current_revision"]
                           or not _same(record["base_values"], record["server_values"])
                           or (base_token is not None and token is not None and base_token != token))
        return DraftSnapshot(*record["scope"], _copy(record["values"]), _copy(record["base_values"]),
            _copy(record["server_values"]), record["base_revision"], record["current_revision"],
            dirty, stale, key, base_token, token)

    def load(self, case_id, editor_id, target_id, revision, initial, *, concurrency_token=None):
        """Refresh clean drafts; retain dirty values and flag concurrent changes.

        The optional token must be computed by the server from the entire
        relevant business snapshot (for example review_digest). An unchanged
        token and unchanged base values allow revision-only download/audit noise
        to rebase a dirty draft. Without a token, revisions remain strict.
        """
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise ValidationError("草稿对应的资料版本无效。")
        if not isinstance(initial, dict):
            raise ValidationError("草稿初始内容须为填写项集合。")
        if concurrency_token is not None and (not isinstance(concurrency_token, str)
                or not concurrency_token or len(concurrency_token) > 512):
            raise ValidationError("草稿对应的业务快照标识无效。")
        key = self._key(case_id, editor_id, target_id)
        values = _copy(initial)
        record = self._records().get(key)
        if record is None or _same(record["values"], record["base_values"]):
            epoch = (record["epoch"] if record is not None and record["base_revision"] == revision
                     and _same(record["base_values"], values) else self._epoch(key))
            record = {"scope": [case_id, editor_id, target_id], "base_revision": revision,
                "current_revision": revision, "base_values": values,
                "server_values": values, "values": values, "epoch": epoch,
                "widget_epochs": record.get("widget_epochs", {}) if record else {},
                "base_concurrency_token": concurrency_token, "current_concurrency_token": concurrency_token}
        else:
            record["current_revision"], record["server_values"] = revision, values
            record["current_concurrency_token"] = concurrency_token
            if (concurrency_token is not None and record.get("base_concurrency_token") == concurrency_token
                    and _same(record["base_values"], values)):
                record["base_revision"] = revision
        return self._put(key, record)

    def update(self, case_id, editor_id, target_id, values):
        """Update a local draft only; this method never accesses a repository."""
        if not isinstance(values, dict):
            raise ValidationError("草稿内容须为填写项集合。")
        key = self._key(case_id, editor_id, target_id)
        record = self._records().get(key)
        if record is None:
            raise ValidationError("此填写位置已关闭，请重新打开后继续。")
        record["values"] = _copy(values)
        return self._put(key, record)

    def patch(self, case_id, editor_id, target_id, changes):
        if not isinstance(changes, dict):
            raise ValidationError("草稿修改项无效。")
        key = self._key(case_id, editor_id, target_id)
        record = self._records().get(key)
        if record is None:
            raise ValidationError("此填写位置已关闭，请重新打开后继续。")
        values = record["values"]
        values.update(_copy(changes))
        return self.update(case_id, editor_id, target_id, values)

    def clear(self, case_id, editor_id, target_id):
        """Call only after a successful explicit save, or explicit discard."""
        records = self._records()
        records.pop(self._key(case_id, editor_id, target_id), None)
        self.state[NAMESPACE] = records

    def discard(self, case_id, editor_id, target_id, revision, initial, *, concurrency_token=None):
        if type(revision) is not int or revision < 0 or not isinstance(initial, dict):
            raise ValidationError("草稿对应的资料版本或初始内容无效。")
        _copy(initial)
        if concurrency_token is not None and (not isinstance(concurrency_token, str)
                or not concurrency_token or len(concurrency_token) > 512):
            raise ValidationError("草稿对应的业务快照标识无效。")
        self.clear(case_id, editor_id, target_id)
        return self.load(case_id, editor_id, target_id, revision, initial, concurrency_token=concurrency_token)

    @staticmethod
    def can_save(snapshot):
        return snapshot.can_save

    def widget_key(self, case_id, editor_id, target_id, field_name):
        """The base revision resets controls only after refresh/discard/save."""
        key = self._key(case_id, editor_id, target_id)
        record = self._records().get(key)
        if record is None:
            raise ValidationError("请先打开草稿填写位置。")
        field = sha256(str(field_name).encode()).hexdigest()[:16]
        field_epoch = record.get("widget_epochs", {}).get(field, 0)
        return f"w3-draft-widget-{key}-{record['base_revision']}-{record['epoch']}-{field}-{field_epoch}"

    def capture_widget(self, case_id, editor_id, target_id, widget_key, field_name):
        """Use as on_change outside st.form, so navigation first captures edits."""
        if widget_key not in self.state:
            return None
        if self._key(case_id, editor_id, target_id) not in self._records():
            return None
        if widget_key.startswith("w3-draft-widget-") and widget_key != self.widget_key(case_id, editor_id, target_id, field_name):
            return None
        return self.patch(case_id, editor_id, target_id, {field_name: self.state[widget_key]})

    def capture_editor(self, case_id, editor_id, target_id, widget_key, field_name, base_rows):
        """Capture one data_editor delta against the exact rows rendered.

        Pass the last rendered row list in on_change args and use widget_key()
        on each render. Capture rotates that field's widget key, so the next
        editor starts with the updated rows and an empty delta. Do not assign
        data_editor widget state: live Streamlit rejects such assignments.
        """
        delta = self.state.get(widget_key)
        if delta is None:
            return None
        if self._key(case_id, editor_id, target_id) not in self._records():
            return None
        if widget_key.startswith("w3-draft-widget-") and widget_key != self.widget_key(case_id, editor_id, target_id, field_name):
            return None
        if not isinstance(delta, dict) or not isinstance(base_rows, list):
            raise ValidationError("草稿明细修改格式无效。")
        rows = _copy(base_rows)
        edits, additions, deletions = delta.get("edited_rows", {}), delta.get("added_rows", []), delta.get("deleted_rows", [])
        if not isinstance(edits, dict) or not isinstance(additions, list) or not isinstance(deletions, list):
            raise ValidationError("草稿明细修改格式无效。")
        for raw_index, changes in edits.items():
            if isinstance(raw_index, bool) or not str(raw_index).isdigit() or not isinstance(changes, dict):
                raise ValidationError("草稿明细修改行无效。")
            index = int(raw_index)
            if index >= len(rows) or not isinstance(rows[index], dict):
                raise ValidationError("草稿明细行已变化，请重新打开填写位置。")
            rows[index].update(_copy(changes))
        if any(type(i) is not int or i < 0 or i >= len(rows) for i in deletions):
            raise ValidationError("草稿明细删除行无效。")
        rows = [row for i, row in enumerate(rows) if i not in set(deletions)]
        if any(not isinstance(row, dict) for row in additions) or len(rows) + len(additions) > 1000:
            raise ValidationError("草稿明细最多支持 1000 行。")
        rows.extend(_copy(additions))
        self.patch(case_id, editor_id, target_id, {field_name: rows})
        key = self._key(case_id, editor_id, target_id)
        record = self._records()[key]
        field = sha256(str(field_name).encode()).hexdigest()[:16]
        epochs = record.setdefault("widget_epochs", {})
        epochs[field] = epochs.get(field, 0) + 1
        return self._put(key, record)

    def list_dirty(self, case_id=None):
        result = [self._snapshot(key, record) for key, record in self._records().items()
                  if case_id is None or record["scope"][0] == case_id]
        return [snapshot for snapshot in result if snapshot.dirty]
