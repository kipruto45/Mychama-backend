from rest_framework.pagination import PageNumberPagination


class DefaultPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


# Backward-compatible alias for existing imports.
class StandardResultsSetPagination(DefaultPagination):
    pass
