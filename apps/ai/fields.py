from __future__ import annotations

import json

from django.db import models

try:
    from pgvector.django import VectorField as PgVectorField
except Exception:  # noqa: BLE001
    PgVectorField = None


if PgVectorField:

    class EmbeddingVectorField(PgVectorField):
        """
        PostgreSQL: true pgvector storage.
        Non-PostgreSQL (e.g. sqlite tests): JSON text fallback.
        """

        def db_type(self, connection):
            if connection.vendor != "postgresql":
                return "text"
            return super().db_type(connection)

        def from_db_value(self, value, expression, connection):
            if connection.vendor != "postgresql":
                if value in (None, ""):
                    return None
                if isinstance(value, str):
                    try:
                        return json.loads(value)
                    except json.JSONDecodeError:
                        return None
            return value

        def get_db_prep_value(self, value, connection, prepared=False):
            if connection.vendor != "postgresql":
                if value is None:
                    return None
                return json.dumps([float(item) for item in value])
            return super().get_db_prep_value(value, connection, prepared)

else:

    class EmbeddingVectorField(models.JSONField):
        """Fallback when pgvector package is unavailable."""

        def __init__(self, *args, dimensions: int = 1536, **kwargs):
            self.dimensions = dimensions
            kwargs.setdefault("null", True)
            kwargs.setdefault("blank", True)
            super().__init__(*args, **kwargs)

        def deconstruct(self):
            name, path, args, kwargs = super().deconstruct()
            kwargs["dimensions"] = self.dimensions
            return name, path, args, kwargs
