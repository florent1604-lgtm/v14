from __future__ import annotations

from pathlib import Path

import yaml

MANIFEST = Path("deploy/k3s/titanium-v14.yaml")


def _documents():
    return [doc for doc in yaml.safe_load_all(MANIFEST.read_text(encoding="utf-8")) if doc]


def test_expected_workloads_and_internal_services_exist():
    docs = _documents()
    deployments = {
        doc["metadata"]["name"]: doc for doc in docs if doc["kind"] == "Deployment"
    }
    services = {doc["metadata"]["name"]: doc for doc in docs if doc["kind"] == "Service"}
    assert set(deployments) == {
        "titanium-api",
        "titanium-deepseek-worker",
        "titanium-mt5-demo-adapter",
    }
    assert set(services) == {"titanium-api", "titanium-mt5-demo-adapter"}
    assert all(service["spec"].get("type", "ClusterIP") == "ClusterIP" for service in services.values())


def test_every_container_has_strict_resources_probes_and_security():
    expected = {
        "titanium-api": ("250m", "512Mi", "500m", "1Gi"),
        "titanium-deepseek-worker": ("250m", "512Mi", "1", "1536Mi"),
        "titanium-mt5-demo-adapter": ("100m", "128Mi", "250m", "256Mi"),
    }
    for doc in _documents():
        if doc["kind"] != "Deployment":
            continue
        name = doc["metadata"]["name"]
        spec = doc["spec"]
        assert spec["replicas"] == 1
        assert spec["strategy"] == {
            "type": "RollingUpdate",
            "rollingUpdate": {"maxSurge": 0, "maxUnavailable": 1},
        }
        pod = spec["template"]["spec"]
        assert pod["securityContext"]["runAsNonRoot"] is True
        container = pod["containers"][0]
        req_cpu, req_mem, lim_cpu, lim_mem = expected[name]
        assert container["resources"]["requests"] == {
            "cpu": req_cpu,
            "memory": req_mem,
            "ephemeral-storage": "128Mi",
        }
        assert container["resources"]["limits"] == {
            "cpu": lim_cpu,
            "memory": lim_mem,
            "ephemeral-storage": "512Mi",
        }
        assert "livenessProbe" in container and "readinessProbe" in container
        security = container["securityContext"]
        assert security["allowPrivilegeEscalation"] is False
        assert security["readOnlyRootFilesystem"] is True
        assert security["capabilities"]["drop"] == ["ALL"]


def test_deepseek_and_mt5_settings_use_secret_refs_and_demo_only():
    text = MANIFEST.read_text(encoding="utf-8")
    assert "sk-" not in text
    assert "kind: Secret" not in text
    deployments = {
        doc["metadata"]["name"]: doc
        for doc in _documents()
        if doc["kind"] == "Deployment"
    }
    deep_env = deployments["titanium-deepseek-worker"]["spec"]["template"]["spec"][
        "containers"
    ][0]["env"]
    by_name = {item["name"]: item for item in deep_env}
    assert by_name["TITANIUM_HERMES_PROVIDER"]["value"] == "deepseek-api"
    assert by_name["TITANIUM_HERMES_MODEL"]["value"] == "deepseek-v4-flash"
    assert by_name["DEEPSEEK_API_KEY"]["valueFrom"]["secretKeyRef"] == {
        "name": "titanium-deepseek",
        "key": "api-key",
    }
    adapter_env = deployments["titanium-mt5-demo-adapter"]["spec"]["template"]["spec"][
        "containers"
    ][0]["env"]
    adapter = {item["name"]: item for item in adapter_env}
    assert adapter["TITANIUM_EXEC_MODE"]["value"] == "DEMO"
    assert adapter["MT5_BRIDGE_URL"]["valueFrom"]["configMapKeyRef"] == {
        "name": "titanium-runtime",
        "key": "mt5-bridge-url",
    }
    assert adapter["MT5_ADAPTER_TOKEN"]["valueFrom"]["secretKeyRef"]["name"] == "titanium-mt5-bridge"
    assert adapter["MT5_BRIDGE_TOKEN"]["valueFrom"]["secretKeyRef"]["name"] == "titanium-mt5-bridge"


def test_namespace_quota_and_default_limits_are_present():
    kinds = {doc["kind"]: doc for doc in _documents() if doc["kind"] in {"ResourceQuota", "LimitRange"}}
    assert kinds["ResourceQuota"]["spec"]["hard"]["limits.memory"] == "3Gi"
    defaults = kinds["LimitRange"]["spec"]["limits"][0]
    assert defaults["type"] == "Container"
    assert defaults["default"]["memory"] == "512Mi"
