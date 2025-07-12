from .data_models import ShiftTemplate


def sample_shifts():
    """Return preset shift templates for test mode."""
    return [
        ShiftTemplate(label="Junior night float", role="Junior", night_float=True, thu_weekend=False),
        ShiftTemplate(label="Senior night float", role="Senior", night_float=True, thu_weekend=False),
        ShiftTemplate(label="ER night", role="Junior", night_float=False, thu_weekend=True),
        ShiftTemplate(label="Ward night", role="Junior", night_float=False, thu_weekend=True),
        ShiftTemplate(label="Senior night", role="Senior", night_float=False, thu_weekend=True),
        ShiftTemplate(label="evening shift", role="Senior", night_float=False, thu_weekend=False),
        ShiftTemplate(label="morning shift", role="Senior", night_float=False, thu_weekend=False),
        ShiftTemplate(label="Ward morning", role="Junior", night_float=False, thu_weekend=False),
        ShiftTemplate(label="ER zone 1 morning", role="Junior", night_float=False, thu_weekend=False),
        ShiftTemplate(label="ER zone 2 morning", role="Junior", night_float=False, thu_weekend=False),
    ]


def sample_names():
    """Return lists of 30 junior and 15 senior names."""
    juniors = [
        "Alice", "Ben", "Carla", "Derek", "Eva", "Frank", "Grace",
        "Hector", "Isla", "Jack", "Kira", "Liam", "Mia", "Noah", "Olivia",
        "Paul", "Quinn", "Rosa", "Sam", "Tara", "Umar", "Violet", "Wes",
        "Xena", "Yara", "Zane", "Aaron", "Bella", "Caleb", "Dana",
    ]
    seniors = [
        "Erin", "Felix", "Gina", "Harold", "Ivy", "Jonah", "Karen",
        "Leo", "Monica", "Nathan", "Opal", "Perry", "Ruth", "Simon", "Tanya",
    ]
    return juniors, seniors
