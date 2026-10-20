import logging
from datetime import datetime, timezone

from django.db import close_old_connections

from apps.usage.proto import usage_als_pb2, usage_als_pb2_grpc
from apps.usage.service import UsageRecord, record_usage


logger = logging.getLogger(__name__)

METADATA_NAMESPACE = 'io.envoy.ai_gateway'
TOKEN_METADATA_KEY = 'llm_total_token'
USER_HEADER = 'x-current-user'
PROJECT_HEADER = 'x-current-project'
PROJECT_ID_HEADER = 'x-current-project-id'
MODEL_HEADER = 'x-ai-eg-model'


def _headers(entry):
    return {key.lower(): value for key, value in entry.request.request_headers.items()}


def _token_count(entry):
    metadata = entry.common_properties.metadata.filter_metadata.get(METADATA_NAMESPACE)
    if metadata is None or TOKEN_METADATA_KEY not in metadata.fields:
        return None

    value = metadata.fields[TOKEN_METADATA_KEY]
    if value.HasField('number_value'):
        number = value.number_value
        if number.is_integer() and number >= 0:
            return int(number)
    if value.HasField('string_value'):
        try:
            number = int(value.string_value)
        except ValueError:
            return None
        return number if number >= 0 else None
    return None


def _response_code(entry):
    if not entry.HasField('response') or not entry.response.HasField('response_code'):
        return None
    return entry.response.response_code.value


def parse_entry(entry, now=None):
    headers = _headers(entry)
    request_id = entry.request.request_id.strip()
    user = headers.get(USER_HEADER, '').strip()
    project = headers.get(PROJECT_HEADER, '').strip()
    route_name = entry.common_properties.route_name.strip()
    model = headers.get(MODEL_HEADER, '').strip() or route_name
    token_count = _token_count(entry)
    if not request_id or not user or not project or not model or token_count is None:
        return None

    try:
        project_identifier = int(headers[PROJECT_ID_HEADER])
    except (KeyError, ValueError):
        return None

    if entry.common_properties.HasField('start_time'):
        occurred_at = entry.common_properties.start_time.ToDatetime(tzinfo=timezone.utc)
    else:
        occurred_at = now or datetime.now(timezone.utc)

    return UsageRecord(
        request_id=request_id,
        user_identifier=user,
        project_identifier=project_identifier,
        project_shortcut=project,
        model_name=model,
        route_name=route_name,
        token_count=token_count,
        response_code=_response_code(entry),
        occurred_at=occurred_at,
    )


class AccessLogService(usage_als_pb2_grpc.AccessLogServiceServicer):
    def StreamAccessLogs(self, request_iterator, context):
        for message in request_iterator:
            close_old_connections()
            try:
                if not message.HasField('http_logs'):
                    continue
                for entry in message.http_logs.log_entry:
                    record = parse_entry(entry)
                    if record is None:
                        logger.warning('Ignored incomplete Envoy usage record')
                        continue
                    record_usage(record)
            finally:
                close_old_connections()
        return usage_als_pb2.StreamAccessLogsResponse()
