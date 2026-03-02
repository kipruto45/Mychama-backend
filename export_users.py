import os
import csv
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')
django.setup()

from django.contrib.auth import get_user_model

User = get_user_model()

with open('users.csv', 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['id','username','email','is_active','is_staff','is_superuser','date_joined'])
    for u in User.objects.all():
        w.writerow([
            str(u.pk),
            getattr(u, 'username', '') or '',
            getattr(u, 'email', '') or '',
            u.is_active,
            u.is_staff,
            u.is_superuser,
            str(getattr(u, 'date_joined', None)),
        ])

print('WROTE users.csv')
