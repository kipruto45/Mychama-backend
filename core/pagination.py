from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class DefaultPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100

    def get_paginated_response(self, data):
        return Response(
            {
                "success": True,
                "results": data,
                "data": data,
                "pagination": {
                    "count": self.page.paginator.count,
                    "page": self.page.number,
                    "page_size": self.get_page_size(self.request),
                    "num_pages": self.page.paginator.num_pages,
                    "next": self.get_next_link(),
                    "previous": self.get_previous_link(),
                },
            }
        )


# Backward-compatible alias for existing imports.
class StandardResultsSetPagination(DefaultPagination):
    pass
