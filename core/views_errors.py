from django.views.generic import TemplateView


class Error400View(TemplateView):
    template_name = 'errors/400.html'
    status_code = 400

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Bad Request'
        context['error_code'] = self.status_code
        return context


class Error403View(TemplateView):
    template_name = 'errors/403.html'
    status_code = 403

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Forbidden'
        context['error_code'] = self.status_code
        return context


class Error404View(TemplateView):
    template_name = 'errors/404.html'
    status_code = 404

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Page Not Found'
        context['error_code'] = self.status_code
        return context


class Error500View(TemplateView):
    template_name = 'errors/500.html'
    status_code = 500

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Internal Server Error'
        context['error_code'] = self.status_code
        return context


# Function-based views for backward compatibility
def error_400_view(request, exception=None):
    return Error400View.as_view()(request)


def error_403_view(request, exception=None):
    return Error403View.as_view()(request)


def error_404_view(request, exception=None):
    return Error404View.as_view()(request)


def error_500_view(request):
    return Error500View.as_view()(request)