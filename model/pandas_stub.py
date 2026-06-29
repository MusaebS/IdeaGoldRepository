class SimpleDataFrame(list):
    """Very small subset of pandas.DataFrame used for testing without pandas."""
    def __init__(self, data=None):
        super().__init__(data or [])
        # Mirror pandas' ``DataFrame.attrs`` so the solver can stash metadata
        # (solver status, time limit, resolved targets) without crashing when
        # pandas is unavailable and this stub stands in for it.
        self.attrs = {}

    def to_dict(self, orient="records"):
        return list(self)

    @property
    def columns(self):
        # Use list.__getitem__ to read the first row dict directly; the
        # overridden __getitem__ below projects a column instead.
        return list(list.__getitem__(self, 0).keys()) if len(self) else []

    def __getitem__(self, key):
        return [row.get(key) for row in self]

# Provide a minimal pd-like namespace
pd = type("pd", (), {"DataFrame": SimpleDataFrame})()
