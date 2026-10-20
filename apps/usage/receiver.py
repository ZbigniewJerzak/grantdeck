import logging
import os
from concurrent import futures

import django
import grpc


DEFAULT_MAX_WORKERS = 256


def max_workers():
    value = os.environ.get('GDK_ALS_MAX_WORKERS', str(DEFAULT_MAX_WORKERS))
    try:
        workers = int(value)
    except ValueError as exc:
        raise ValueError('GDK_ALS_MAX_WORKERS must be a positive integer') from exc
    if workers < 1:
        raise ValueError('GDK_ALS_MAX_WORKERS must be a positive integer')
    return workers


def main():
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'grantdeck.settings')
    django.setup()
    from apps.usage import als
    from apps.usage.proto import usage_als_pb2_grpc
    port = int(os.environ.get('GDK_ALS_PORT', '8001'))
    workers = max_workers()
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=workers))
    usage_als_pb2_grpc.add_AccessLogServiceServicer_to_server(
        als.AccessLogService(),
        server,
    )
    server.add_insecure_port(f'[::]:{port}')
    server.start()
    logging.getLogger('apps.usage').info(
        'Envoy ALS receiver listening on %s with %s workers', port, workers
    )
    server.wait_for_termination()


if __name__ == '__main__':
    main()
