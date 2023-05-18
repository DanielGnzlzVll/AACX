import random
import time


def latency_simulator_middleware(get_response):
    # One-time configuration and initialization.

    def middleware(request):
        # Code to be executed for each request before
        # the view (and later middleware) are called.
        # time.sleep(random.randrange(200, 1200) / 1000)
        response = get_response(request)

        # Code to be executed for each request/response after
        # the view is called.

        return response

    return middleware
