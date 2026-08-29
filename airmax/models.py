from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    """Empty today. Swapping the user model once rows exist is a data migration, so it
    costs nothing now and a great deal later."""
