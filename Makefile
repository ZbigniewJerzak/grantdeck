.PHONY: venv run test generate_als_proto deploy release

venv: .venv/.installed

.venv/bin/python:
	python3 -m venv .venv

.venv/.installed: pyproject.toml | .venv/bin/python
	.venv/bin/python -m pip install -e .
	touch .venv/.installed

.venv/.dev-installed: pyproject.toml | .venv/bin/python
	.venv/bin/python -m pip install -e '.[dev]'
	touch .venv/.dev-installed

run: venv
	set -a; [ ! -f .env ] || . ./.env; set +a; .venv/bin/python manage.py runserver

test: .venv/.dev-installed
	.venv/bin/python -m pytest

generate_als_proto: .venv/.dev-installed
	.venv/bin/python -m grpc_tools.protoc -I proto --python_out=apps/usage/proto --grpc_python_out=apps/usage/proto proto/usage_als.proto
	sed -i.bak 's/^import usage_als_pb2 as usage__als__pb2$$/from . import usage_als_pb2 as usage__als__pb2/' apps/usage/proto/usage_als_pb2_grpc.py
	rm -f apps/usage/proto/usage_als_pb2_grpc.py.bak

deploy:
	kubectl apply -k deploy/k8s/overlays/production

release:
	@test -n "$(VERSION)" || { echo 'Usage: make release VERSION=1.2.3' >&2; exit 1; }
	@printf '%s\n' "$(VERSION)" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$$' || { echo 'VERSION must use MAJOR.MINOR.PATCH, for example 1.2.3' >&2; exit 1; }
	git tag -a "v$(VERSION)" -m "Release v$(VERSION)"
	git push origin "v$(VERSION)"
