"""Finite compatibility descriptors for state owned by domain controllers."""


def state_field(owner, field):
    def read(instance):
        return getattr(getattr(instance, owner), field)

    def write(instance, value):
        setattr(getattr(instance, owner), field, value)

    return property(read, write)
