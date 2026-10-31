"""Transaction requirements grouped by supplied input and delivered output.

Groups describe purpose, not the origin or completeness of any actual record.
An explicit input may also be a seller worksheet; one type may serve both roles.
"""
from .catalog import SELLER_TYPES


def requirement_groups(workspace):
    """Return effective groups, including applicable conditional requirements.

    Old snapshots have no purpose selections. Infer their grouping without
    modifying the snapshot. Policy additions keep their default type grouping
    unless the user has already explicitly assigned that type to either group.
    """
    if workspace.required_source_types is None and workspace.required_delivery_types is None:
        return ([kind for kind in workspace.required_document_types if kind not in SELLER_TYPES],
                [kind for kind in workspace.required_document_types if kind in SELLER_TYPES])
    sources = list(dict.fromkeys(workspace.required_source_types or []))
    deliveries = list(dict.fromkeys(workspace.required_delivery_types or []))
    for kind in workspace.required_document_types:
        if kind not in sources and kind not in deliveries:
            (deliveries if kind in SELLER_TYPES else sources).append(kind)
    return sources, deliveries
