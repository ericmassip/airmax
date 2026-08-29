from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView


class Home(LoginRequiredMixin, TemplateView):
    """Placeholder for the map. Exists now so the skeleton proves auth, templates and the
    built asset pipeline end to end before anything depends on them."""

    template_name = "airmax/home.html"
