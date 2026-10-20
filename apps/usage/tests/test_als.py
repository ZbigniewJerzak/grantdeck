from datetime import datetime, timezone

from apps.usage.als import METADATA_NAMESPACE, TOKEN_METADATA_KEY, parse_entry
from apps.usage.proto import usage_als_pb2


def test_parse_entry_extracts_identity_and_token_metadata():
    entry = usage_als_pb2.HTTPAccessLogEntry()
    entry.common_properties.route_name = 'route-large'
    entry.common_properties.start_time.FromDatetime(datetime(2026, 9, 26, 10, 0, tzinfo=timezone.utc))
    entry.request.request_id = 'request-123'
    entry.request.request_headers['x-current-user'] = 'alice'
    entry.request.request_headers['x-current-project'] = 'research'
    entry.request.request_headers['x-current-project-id'] = '7'
    entry.request.request_headers['x-ai-eg-model'] = 'bht/large'
    entry.common_properties.metadata.filter_metadata[METADATA_NAMESPACE].fields[TOKEN_METADATA_KEY].number_value = 123
    entry.response.response_code.value = 200

    record = parse_entry(entry)

    assert record.user_identifier == 'alice'
    assert record.project_identifier == 7
    assert record.project_shortcut == 'research'
    assert record.model_name == 'bht/large'
    assert record.token_count == 123
    assert record.response_code == 200


def test_parse_entry_rejects_records_without_project_id():
    entry = usage_als_pb2.HTTPAccessLogEntry()
    entry.request.request_id = 'request-123'
    entry.request.request_headers['x-current-user'] = 'alice'
    entry.request.request_headers['x-current-project'] = 'research'
    entry.common_properties.route_name = 'route-large'
    entry.common_properties.metadata.filter_metadata[METADATA_NAMESPACE].fields[TOKEN_METADATA_KEY].number_value = 1

    assert parse_entry(entry) is None


def test_stream_releases_database_connections_between_messages(monkeypatch):
    from apps.usage import als

    close_connections = []
    monkeypatch.setattr(als, 'close_old_connections', lambda: close_connections.append(True))
    monkeypatch.setattr(als, 'record_usage', lambda record: True)

    message = usage_als_pb2.StreamAccessLogsMessage()
    entry = message.http_logs.log_entry.add()
    entry.common_properties.route_name = 'route-large'
    entry.request.request_id = 'request-123'
    entry.request.request_headers['x-current-user'] = 'alice'
    entry.request.request_headers['x-current-project'] = 'research'
    entry.request.request_headers['x-current-project-id'] = '7'
    entry.common_properties.metadata.filter_metadata[METADATA_NAMESPACE].fields[
        TOKEN_METADATA_KEY
    ].number_value = 123

    als.AccessLogService().StreamAccessLogs(iter([message]), None)

    assert close_connections == [True, True]
