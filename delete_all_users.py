import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')
django.setup()

from django.contrib.auth import get_user_model
from django.db import connection

User = get_user_model()
table = User._meta.db_table
print('USER TABLE:', table)
with connection.cursor() as c:
    # Temporarily disable foreign key checks (SQLite) to allow raw delete
    try:
        c.execute("PRAGMA foreign_keys=OFF")
    except Exception:
        pass
    c.execute(f"DELETE FROM {table}")
    try:
        c.execute("PRAGMA foreign_keys=ON")
    except Exception:
        pass
print('RAW DELETE executed')
print('Remaining users:', User.objects.count())
