"""Domain contracts share the same source, revision and review machinery."""

def load(name):
    if name == 'generic':
        from . import generic
        return generic
    if name == 'lung-pathology':
        from .. import schema
        return schema
    raise ValueError(f'Unknown profile: {name}')
