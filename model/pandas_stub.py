class SimpleDataFrame(list):
    """Very small subset of pandas.DataFrame used for testing without pandas."""
    def __init__(self, data=None):
        super().__init__(data or [])

    def to_dict(self, orient="records"):
        return list(self)

    def __getitem__(self, key):
        return [row.get(key) for row in self]

# Provide a minimal pd-like namespace
pd = type("pd", (), {"DataFrame": SimpleDataFrame})()
