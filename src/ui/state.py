"""Coalesce scalar state updates before touching Qt widgets."""
from dataclasses import fields, replace


def scalar_changes(widget, state):
    previous = getattr(widget, '_last_scalar_state', {})
    changed = {}
    for field in fields(state):
        value = getattr(state, field.name)
        if value is None:
            continue
        # Centering is a command, not a persistent state value.
        if field.name == 'set_x_to_center':
            if value:
                changed[field.name] = True
        elif field.name not in previous or previous[field.name] != value:
            changed[field.name] = value
            previous[field.name] = value
    widget._last_scalar_state = previous
    if not changed:
        return None
    # Geometry setters consume a complete rectangle (or x/y pair). A width-only
    # change must not lose the unchanged x/y values needed to apply it.
    geometry = {'x', 'y', 'w', 'h'}
    if geometry.intersection(changed):
        for field in fields(state):
            if field.name in geometry and field.name in previous:
                changed[field.name] = previous[field.name]
    return replace(state, **{field.name: changed.get(field.name, False if field.name == 'set_x_to_center' else None)
                             for field in fields(state)})
