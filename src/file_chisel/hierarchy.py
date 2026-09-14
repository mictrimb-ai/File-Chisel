"""Build folder relationships from existing scan metadata."""


def index_children(entries):
    children = {}

    # Your loop goes here.

    for entry in entries:
        parent = entry.path.parent

        if parent not in children:
            children[parent] = [entry]
        else:
            children[parent].append(entry)

    return children