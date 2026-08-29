import pytest
from django.contrib.auth import get_user_model


@pytest.mark.django_db
def test_a_user_can_be_created_and_read_back():
    """Trivial by design — what it actually proves is that pytest-django can build and
    migrate a test database on RDS, which everything else will depend on."""
    get_user_model().objects.create_user(username="ann", password="hunter2hunter2")

    assert list(get_user_model().objects.values_list("username", flat=True)) == ["ann"]
