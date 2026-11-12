"""The sandbox Docker proxy forwards sandbox containers and nothing else.

The proxy holds the Docker daemon's socket; the application container does
not (``core/tool/sandbox/docker_proxy.py``). It accepts one strict form of
a request, checks every value, and sends the daemon a request it built
itself: the daemon never sees the client's text.
"""

from __future__ import annotations

import asyncio
import copy
import json
import tempfile
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from fim_one.core.tool.sandbox.docker_proxy import Policy, policy_from_env, serve

pytestmark = pytest.mark.timeout(30)

ROOT = "/srv/fim/data/sandbox"
IMAGE = "fim-sandbox:python"
POLICY = Policy(images=frozenset({IMAGE, "node:20-slim"}), sandbox_root=ROOT)
NAME = "fim-sandbox-0123abcd"
CREATE = f"/v1.47/containers/create?name={NAME}"
TOKEN = "t" * 40
ENV = {"DOCKER_HOST_DATA_DIR": "/srv/fim/data", "SANDBOX_PROXY_TOKEN": TOKEN}
BIND = f"{ROOT}/conv-1/workspace:/workspace"


def _body(**host: Any) -> dict[str, Any]:
    """What ``docker run`` sends for the flags ``docker_backend.py`` passes."""
    host_config: dict[str, Any] = {
        "Binds": [BIND],
        "NetworkMode": "none",
        "Memory": 268435456,
        "NanoCpus": 500000000,
        "PidsLimit": 256,
        "AutoRemove": True,
        "SecurityOpt": ["no-new-privileges"],
        "CapDrop": ["ALL"],
        "LogConfig": {"Type": "", "Config": {}},
        "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0},
        "ConsoleSize": [0, 0],
        "Privileged": False,
        "PidMode": "",
        "IpcMode": "",
        "UTSMode": "",
        "UsernsMode": "",
        "CapAdd": None,
        "Devices": [],
        "Mounts": None,
        "PortBindings": {},
        "MaskedPaths": None,
        "ReadonlyPaths": None,
        "MemorySwappiness": -1,
    }
    host_config.update(host)
    return {
        "Image": IMAGE,
        "User": "10001:10001",
        "Env": ["HOME=/tmp"],
        "Cmd": ["python", "/workspace/script_1.py"],
        "WorkingDir": "/workspace",
        "AttachStdout": True,
        "OpenStdin": True,
        "Volumes": {},
        "ExposedPorts": {},
        "Labels": {},
        "HostConfig": host_config,
        "NetworkingConfig": {"EndpointsConfig": {}},
    }


def _create(body: Any, target: str = CREATE, policy: Policy = POLICY) -> Any:
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    return policy.authorize("POST", target, raw)


class TestSandboxContainerIsAllowed:
    def test_what_the_backend_asks_for(self) -> None:
        assert _create(_body()).allowed

    def test_a_subdirectory_of_the_workspace(self) -> None:
        body = _body()
        body["WorkingDir"] = "/workspace/sub/dir"
        body["NetworkingConfig"] = {"EndpointsConfig": {"none": {"IPAMConfig": None}}}

        assert _create(body).allowed

    def test_the_operators_egress_network_and_no_other(self) -> None:
        policy = Policy(images=POLICY.images, sandbox_root=ROOT, egress_network="fim-egress")

        verdict = _create(_body(NetworkMode="fim-egress"), policy=policy)

        # Allowed once the server has looked the network's label up.
        assert verdict.allowed and verdict.network == "fim-egress"
        assert not _create(_body(NetworkMode="bridge"), policy=policy).allowed
        assert not _create(_body(NetworkMode="fim-egress")).allowed

    @pytest.mark.parametrize(
        ("method", "target"),
        [
            ("HEAD", "/_ping"),
            ("GET", "/v1.47/_ping"),
            ("GET", "/v1.47/version"),
            ("POST", f"/v1.47/containers/{NAME}/start"),
            ("POST", f"/v1.47/containers/{NAME}/wait?condition=next-exit"),
            ("POST", f"/v1.47/containers/{NAME}/wait?condition=removed"),
            ("POST", f"/v1.47/containers/{NAME}/attach?stream=1&stdin=1&stdout=1&stderr=1"),
            ("POST", f"/v1.47/containers/{NAME}/kill?signal=KILL"),
            ("POST", f"/v1.47/containers/{NAME}/kill"),
            ("DELETE", f"/v1.47/containers/{NAME}?force=1"),
            ("POST", "/v1.47/images/create?fromImage=fim-sandbox&tag=python"),
            ("POST", "/v1.47/images/create?fromImage=node&tag=20-slim"),
        ],
    )
    def test_the_requests_around_it(self, method: str, target: str) -> None:
        assert POLICY.authorize(method, target, b"").allowed

    def test_a_container_id_is_allowed_once_its_name_is_checked(self) -> None:
        verdict = POLICY.authorize("POST", f"/v1.47/containers/{'ab' * 32}/start", b"")

        assert verdict.allowed and verdict.container_id == "ab" * 32


class TestWhatIsSentIsBuiltByTheProxy:
    """Nothing of the client's text reaches the daemon."""

    def test_the_create_body_has_only_the_fields_the_proxy_sets(self) -> None:
        verdict = _create(_body())

        assert verdict.body is not None
        sent = json.loads(verdict.body)
        assert sent == {
            "Image": IMAGE,
            "User": "10001:10001",
            "WorkingDir": "/workspace",
            "Env": ["HOME=/tmp"],
            "Cmd": ["python", "/workspace/script_1.py"],
            "AttachStdin": False,
            "AttachStdout": True,
            "AttachStderr": False,
            "OpenStdin": True,
            "StdinOnce": False,
            "HostConfig": {
                "Binds": [BIND],
                "NetworkMode": "none",
                "Memory": 268435456,
                "NanoCpus": 500000000,
                "PidsLimit": 256,
                "OomScoreAdj": 0,
                "AutoRemove": True,
                "SecurityOpt": ["no-new-privileges"],
                "CapDrop": ["ALL"],
            },
        }
        assert (verdict.method, verdict.target) == ("POST", CREATE)
        assert verdict.headers == (("Content-Type", "application/json"),)

    def test_the_target_is_rebuilt_from_validated_parts(self) -> None:
        verdict = POLICY.authorize(
            "POST", f"/v1.47/containers/{NAME}/attach?stdout=1&stream=1&stdin=1", b""
        )

        assert verdict.target == f"/v1.47/containers/{NAME}/attach?stdin=1&stdout=1&stream=1"

    def test_an_encoded_image_name_is_decoded_once_and_encoded_again(self) -> None:
        policy = Policy(
            images=frozenset({"registry.example.com:5000/team/box:1"}), sandbox_root=ROOT
        )

        verdict = policy.authorize(
            "POST", "/images/create?fromImage=registry.example.com%3A5000%2Fteam%2Fbox&tag=1", b""
        )

        assert verdict.allowed
        assert verdict.target == (
            "/images/create?fromImage=registry.example.com%3A5000%2Fteam%2Fbox&tag=1"
        )


class TestTwoReadingsOfOneRequest:
    """A request has one accepted form; anything else is refused, not interpreted."""

    def test_a_repeated_key_is_refused_not_resolved(self) -> None:
        """A key may appear once in an object."""
        first = json.dumps({"Binds": ["/srv:/mnt"], "Privileged": True})
        good = json.dumps(_body()["HostConfig"] | {"Binds": None})
        raw = (
            f'{{"Image": "{IMAGE}", "User": "10001:10001", '
            f'"HostConfig": {first}, "HostConfig": {good}}}'
        ).encode()
        # A plain parse would silently keep one of the two.
        assert json.loads(raw)["HostConfig"]["Binds"] is None

        verdict = _create(raw)

        assert not verdict.allowed and "twice" in verdict.reason

    def test_a_key_repeated_in_another_case_is_refused(self) -> None:
        """Keys are compared without regard to case."""
        raw = json.dumps(_body())[:-1].encode() + b', "hostconfig": {"Privileged": true}}'

        verdict = _create(raw)

        assert not verdict.allowed and "twice" in verdict.reason

    def test_a_repeated_key_deep_inside_is_refused(self) -> None:
        raw = json.dumps(_body()).replace(
            '"Privileged": false', '"Privileged": false, "privileged": true'
        )

        assert '"privileged": true' in raw and not _create(raw.encode()).allowed

    def test_a_field_in_another_case_is_judged_as_the_field_it_is(self) -> None:
        body = _body()
        body["hostconfig"] = body.pop("HostConfig") | {"PRIVILEGED": True}

        verdict = _create(body)

        assert not verdict.allowed and "privileged" in verdict.reason.lower()

    def test_a_body_in_another_case_is_sent_in_the_proxys_own_spelling(self) -> None:
        body = _body()
        body["hostconfig"] = {key.upper(): value for key, value in body.pop("HostConfig").items()}

        verdict = _create(body)

        assert verdict.allowed and verdict.body is not None
        assert json.loads(verdict.body)["HostConfig"]["Binds"] == [BIND]

    @pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
    def test_what_is_not_json_is_refused(self, constant: str) -> None:
        raw = json.dumps(_body()).replace("268435456", constant).encode()

        assert not _create(raw).allowed

    @pytest.mark.parametrize(
        ("target", "body"),
        [
            # One value per parameter.
            ("/v1.47/images/create?fromImage=alpine&fromImage=fim-sandbox&tag=python", b""),
            ("/v1.47/images/create?fromImage=fim-sandbox&tag=latest&tag=python", b""),
            (f"/v1.47/containers/create?name=other&name={NAME}", b"{}"),
            (f"/v1.47/containers/{NAME}/kill?signal=KILL&signal=TERM", b""),
        ],
    )
    def test_a_repeated_query_parameter_is_refused(self, target: str, body: bytes) -> None:
        verdict = POLICY.authorize("POST", target, body)

        assert not verdict.allowed and "twice" in verdict.reason

    @pytest.mark.parametrize(
        "head",
        [
            # A line ends at CRLF and nowhere else.
            b"GET /_ping HTTP/1.1\r\nX-A: a\nX-B: b\r\n\r\n",
            b"GET /_ping HTTP/1.1\r\nX-A: a\nTransfer-Encoding: chunked\r\n\r\n",
            b"GET /_ping HTTP/1.1\r\nX-A: a\rX-B: b\r\n\r\n",
            b"GET /_ping HTTP/1.1\nHost: x\r\n\r\n",
            b"GET /_ping HTTP/1.1\r\nX-A: a\x00b\r\n\r\n",
            b"GET /_ping HTTP/1.1\r\nX-A: a\tb\r\n\r\n",
            b"GET /_ping HTTP/1.1\r\nX-A: caf\xc3\xa9\r\n\r\n",
            b"GET /_ping HTTP/1.1\r\n folded: x\r\n\r\n",
            b"GET /_ping HTTP/1.1\r\nX A: x\r\n\r\n",
            b"GET /_ping HTTP/1.1\r\nX-A : x\r\n\r\n",
            b"GET /_ping HTTP/1.1\r\nno-colon\r\n\r\n",
            b"GET /_ping HTTP/1.1\r\nHost: a\r\nhost: b\r\n\r\n",
            b"GET /_ping HTTP/1.1\r\nContent-Length: 0\r\nContent-Length: 5\r\n\r\n",
            b"GET  /_ping HTTP/1.1\r\n\r\n",
            b"GET /_ping HTTP/1.0\r\n\r\n",
            b"GET /_ping HTTP/2\r\n\r\n",
            b"PUT /_ping HTTP/1.1\r\n\r\n",
            b"get /_ping HTTP/1.1\r\n\r\n",
            b"GET http://other.example/_ping HTTP/1.1\r\n\r\n",
            b"GET /_ping HTTP/1.1\r\n",
        ],
    )
    def test_a_head_that_can_be_read_two_ways_is_refused(self, head: bytes) -> None:
        from fim_one.core.tool.sandbox.docker_proxy import ParseError, parse_head

        with pytest.raises(ParseError):
            parse_head(head)

    def test_a_plain_head_is_read(self) -> None:
        from fim_one.core.tool.sandbox.docker_proxy import parse_head

        head = b"HEAD /_ping HTTP/1.1\r\nHost: docker\r\nUser-Agent:  Docker \r\n\r\n"

        assert parse_head(head) == ("HEAD", "/_ping", {"host": "docker", "user-agent": "Docker"})


class TestEverythingElseIsRefused:
    @pytest.mark.parametrize(
        ("host", "why"),
        [
            ({"Binds": ["/srv:/mnt"]}, "/workspace"),
            ({"Binds": ["/srv:/workspace"]}, "sandbox workspace"),
            ({"Binds": ["/etc:/workspace"]}, "sandbox workspace"),
            ({"Binds": ["/var/run/docker.sock:/workspace"]}, "sandbox workspace"),
            ({"Binds": [f"{ROOT}:/workspace"]}, "sandbox workspace"),
            # The workspace itself, not the directory above it.
            ({"Binds": [f"{ROOT}/conv-1:/workspace"]}, "sandbox workspace"),
            ({"Binds": [f"{ROOT}/../../../../workspace:/workspace"]}, "sandbox workspace"),
            ({"Binds": [f"{ROOT}/conv-1/../../../workspace:/workspace"]}, "sandbox workspace"),
            ({"Binds": [f"{ROOT}/conv-1/workspace/sub:/workspace"]}, "sandbox workspace"),
            ({"Binds": [f"{ROOT}/conv-1/workspace/:/workspace"]}, "sandbox workspace"),
            ({"Binds": [f"{ROOT}-other/conv-1/workspace:/workspace"]}, "sandbox workspace"),
            ({"Binds": [BIND, f"{ROOT}/conv-2/workspace:/workspace"]}, "exactly one"),
            ({"Binds": []}, "exactly one"),
            ({"Binds": None}, "exactly one"),
            ({"Binds": [BIND + ":rw,rshared"]}, "/workspace"),
            ({"Binds": [BIND + ":ro"]}, "/workspace"),
            ({"Binds": ["named-volume:/workspace"]}, "sandbox workspace"),
            ({"Privileged": True}, "Privileged"),
            ({"PidMode": "host"}, "PidMode"),
            ({"IpcMode": "host"}, "IpcMode"),
            ({"UTSMode": "host"}, "UTSMode"),
            ({"UsernsMode": "host"}, "UsernsMode"),
            ({"NetworkMode": "host"}, "NetworkMode"),
            ({"NetworkMode": "container:fim-one-fim-one-1"}, "NetworkMode"),
            ({"NetworkMode": "fim-one_default"}, "NetworkMode"),
            # A sandbox has no network.
            ({"NetworkMode": "bridge"}, "NetworkMode"),
            ({"NetworkMode": "default"}, "NetworkMode"),
            ({"NetworkMode": ""}, "NetworkMode"),
            ({"NetworkMode": None}, "NetworkMode"),
            ({"CapAdd": ["SYS_ADMIN"]}, "CapAdd"),
            ({"Devices": [{"PathOnHost": "/dev/sda", "PathInContainer": "/dev/sda"}]}, "Devices"),
            ({"Mounts": [{"Type": "bind", "Source": "/", "Target": "/host"}]}, "Mounts"),
            ({"VolumesFrom": ["fim-one-fim-one-1"]}, "VolumesFrom"),
            ({"SecurityOpt": ["seccomp=unconfined"]}, "SecurityOpt"),
            ({"SecurityOpt": ["apparmor=unconfined", "no-new-privileges"]}, "SecurityOpt"),
            ({"Runtime": "sysbox-runc"}, "Runtime"),
            ({"CgroupParent": "/"}, "CgroupParent"),
            ({"PortBindings": {"80/tcp": [{"HostPort": "80"}]}}, "PortBindings"),
            ({"PublishAllPorts": True}, "PublishAllPorts"),
            ({"MaskedPaths": []}, "MaskedPaths"),
            ({"ReadonlyPaths": []}, "ReadonlyPaths"),
            ({"RestartPolicy": {"Name": "always"}}, "RestartPolicy"),
            ({"Sysctls": {"kernel.core_pattern": "|/x"}}, "Sysctls"),
            ({"GroupAdd": ["0"]}, "GroupAdd"),
            ({"Tmpfs": {"/workspace": ""}}, "Tmpfs"),
            (
                {
                    "LogConfig": {
                        "Type": "syslog",
                        "Config": {"syslog-address": "tcp://other.example"},
                    }
                },
                "Log",
            ),
            ({"SomethingNewerDockerAdds": "x"}, "SomethingNewerDockerAdds"),
            # The hardening is required, not merely permitted.
            ({"Memory": 0}, "Memory"),
            ({"Memory": None}, "Memory"),
            ({"Memory": 64 * 1024**3}, "Memory"),
            ({"Memory": True}, "Memory"),
            ({"NanoCpus": 0}, "NanoCpus"),
            ({"NanoCpus": 64 * 10**9}, "NanoCpus"),
            ({"PidsLimit": 0}, "PidsLimit"),
            ({"PidsLimit": -1}, "PidsLimit"),
            ({"PidsLimit": None}, "PidsLimit"),
            ({"PidsLimit": 10**6}, "PidsLimit"),
            ({"SecurityOpt": []}, "SecurityOpt"),
            ({"SecurityOpt": None}, "SecurityOpt"),
            ({"CapDrop": []}, "CapDrop"),
            ({"CapDrop": None}, "CapDrop"),
            ({"CapDrop": ["NET_RAW"]}, "CapDrop"),
            ({"OomScoreAdj": -1000}, "OomScoreAdj"),
            ({"MemorySwap": -1}, "MemorySwap"),
            ({"MemorySwappiness": 100}, "MemorySwappiness"),
        ],
    )
    def test_host_config(self, host: dict[str, Any], why: str) -> None:
        verdict = _create(_body(**host))

        assert not verdict.allowed and why.lower() in verdict.reason.lower()

    @pytest.mark.parametrize(
        ("change", "why"),
        [
            ({"Image": "alpine"}, "image"),
            ({"Image": "docker:27-cli"}, "image"),
            ({"Image": None}, "image"),
            ({"User": "root"}, "User"),
            ({"User": ""}, "User"),
            ({"User": None}, "User"),
            ({"User": "0"}, "User"),
            ({"User": "0:0"}, "User"),
            ({"User": "10001:0"}, "User"),
            # Not root, and still not the client's to choose.
            ({"User": "1000:1000"}, "User"),
            ({"User": "10001:999"}, "User"),
            ({"User": "10001"}, "User"),
            ({"WorkingDir": "/"}, "WorkingDir"),
            ({"WorkingDir": "/workspace/../etc"}, "WorkingDir"),
            ({"WorkingDir": "/workspace/./x"}, "WorkingDir"),
            ({"Volumes": {"/host": {}}}, "Volumes"),
            ({"ExposedPorts": {"80/tcp": {}}}, "ExposedPorts"),
            ({"Cmd": "sh -c id"}, "Cmd"),
            ({"Labels": {"com.docker.compose.project": "fim-one"}}, "Labels"),
            ({"Tty": True}, "Tty"),
            ({"Hostname": "postgres"}, "Hostname"),
            ({"HostConfig": None}, "HostConfig"),
            ({"NetworkingConfig": {"EndpointsConfig": {"fim-one_default": {}}}}, "network"),
            ({"SomethingNew": {"x": 1}}, "SomethingNew"),
        ],
    )
    def test_container_config(self, change: dict[str, Any], why: str) -> None:
        body = _body()
        body.update(change)

        verdict = _create(body)

        assert not verdict.allowed and why.lower() in verdict.reason.lower()

    @pytest.mark.parametrize(
        ("method", "target"),
        [
            ("GET", "/v1.47/containers/json"),
            ("GET", "/v1.47/info"),
            ("POST", "/v1.47/containers/fim-one-fim-one-1/exec"),
            ("POST", f"/v1.47/containers/{NAME}/exec"),
            ("GET", f"/v1.47/containers/{NAME}/archive?path=/"),
            ("PUT", f"/v1.47/containers/{NAME}/archive?path=/"),
            ("POST", "/v1.47/containers/fim-one-postgres-1/kill"),
            ("DELETE", "/v1.47/containers/fim-one-postgres-1"),
            ("POST", "/v1.47/exec/abc/start"),
            ("POST", "/v1.47/images/create?fromImage=alpine&tag=latest"),
            (
                "POST",
                "/v1.47/images/create?fromSrc=http://other.example/x.tar&repo=fim-sandbox&tag=python",
            ),
            ("POST", "/v1.47/images/create?fromImage=fim-sandbox&tag=python&fromSrc=x"),
            ("POST", "/v1.47/images/create?fromImage=fim-sandbox@sha256:abc"),
            # Without a tag the daemon would pull every tag of the repository.
            ("POST", "/v1.47/images/create?fromImage=fim-sandbox"),
            ("POST", "/v1.47/images/create?fromImage=fim-sandbox&tag="),
            ("POST", "/v1.47/images/create?fromImage=fim-sandbox&tag=python&platform=linux"),
            ("POST", "/v1.47/images/create?fromImage=node&tag=20-slim%00"),
            ("POST", "/v1.47/images/create?fromImage=node&tag=20+slim"),
            ("POST", "/v1.47/images/create?fromImage=node&tag=20-slim%2"),
            ("POST", "/v1.47/build"),
            ("POST", "/v1.47/volumes/create"),
            ("POST", "/v1.47/networks/create"),
            ("POST", "/v1.47/swarm/init"),
            ("POST", "/v1.47/services/create"),
            ("POST", "/v1.47/plugins/pull"),
            ("POST", f"/v1.47/containers/{NAME}/../../images/create"),
            ("POST", f"/v1.47/containers/%2e%2e/{NAME}/start"),
            ("POST", f"/v1.47//containers/{NAME}/start"),
            ("GET", "http://other.example/v1.47/_ping"),
            ("POST", f"/v1.47/containers/{NAME}/resize?h=1&w=1"),
            ("POST", f"/v1.47/containers/{NAME}/attach?stream=1&detachKeys=ctrl-a"),
            ("POST", f"/v1.47/containers/{NAME}/wait?condition=whatever"),
            ("POST", f"/v1.47/containers/{NAME}/start?x=1"),
            ("PUT", "/v1.47/_ping"),
            ("PATCH", "/v1.47/_ping"),
            ("GET", "/v1.47/_ping?x=1"),
        ],
    )
    def test_requests(self, method: str, target: str) -> None:
        assert not POLICY.authorize(method, target, b"").allowed

    @pytest.mark.parametrize(
        "target",
        [
            "/v1.47/containers/create",
            "/v1.47/containers/create?name=fim-one-fim-one-1",
            f"/v1.47/containers/create?name={NAME}&platform=linux",
        ],
    )
    def test_a_sandbox_has_a_sandbox_name(self, target: str) -> None:
        assert not _create(_body(), target).allowed

    def test_a_body_that_is_not_an_object(self) -> None:
        assert not POLICY.authorize("POST", CREATE, b"[]").allowed
        assert not POLICY.authorize("POST", CREATE, b"{").allowed
        assert not POLICY.authorize("POST", CREATE, b"\xff\xfe{}").allowed

    def test_a_body_where_none_belongs(self) -> None:
        assert not POLICY.authorize("POST", f"/containers/{NAME}/start", b"{}").allowed

    def test_everything_when_the_backend_is_not_docker(self) -> None:
        off = policy_from_env({**ENV, "CODE_EXEC_BACKEND": "local"})

        assert not off.authorize("GET", "/_ping", b"").allowed
        assert not _create(_body(), policy=off).allowed

    def test_pulls_can_be_switched_off(self) -> None:
        off = policy_from_env({**ENV, "SANDBOX_PROXY_ALLOW_PULL": "false"})

        assert not off.authorize("POST", "/images/create?fromImage=node&tag=20-slim", b"").allowed


class TestPolicyFromEnvironment:
    def test_the_images_and_the_mount_root(self) -> None:
        policy = policy_from_env(
            {
                **ENV,
                "DOCKER_HOST_DATA_DIR": "/srv/fim/data/",
                "DOCKER_PYTHON_IMAGE": IMAGE,
                "DOCKER_SHELL_IMAGE": IMAGE,
                "SANDBOX_PROXY_EXTRA_IMAGES": "a:1, b:2",
            }
        )

        assert policy.sandbox_root == ROOT and policy.enabled and policy.token == TOKEN
        assert policy.images == {IMAGE, "node:20-slim", "a:1", "b:2"}
        assert policy.egress_network is None

    def test_sandboxes_are_labelled_by_the_proxy(self) -> None:
        unnamed = policy_from_env(ENV)
        named = policy_from_env({**ENV, "SANDBOX_PROXY_PROJECT": "fim-one"})
        odd = policy_from_env({**ENV, "SANDBOX_PROXY_PROJECT": "Not A Project"})

        assert unnamed.labels == odd.labels == (("fim-one.sandbox", "1"),)
        assert named.labels == (("fim-one.sandbox", "1"), ("fim-one.project", "fim-one"))
        sent = json.loads(_create(_body(), policy=replace(POLICY, labels=named.labels)).body)
        assert sent["Labels"] == {"fim-one.sandbox": "1", "fim-one.project": "fim-one"}

    def test_the_lifetime_limit(self) -> None:
        assert policy_from_env(ENV).max_lifetime == 1800
        assert policy_from_env({**ENV, "SANDBOX_PROXY_MAX_LIFETIME": "600"}).max_lifetime == 600

    def test_no_data_directory_is_an_error(self) -> None:
        with pytest.raises(ValueError, match="DOCKER_HOST_DATA_DIR"):
            policy_from_env({"SANDBOX_PROXY_TOKEN": TOKEN})

    def test_the_sandbox_user_is_set_on_the_proxy(self) -> None:
        images = {"DOCKER_PYTHON_IMAGE": IMAGE, "DOCKER_SHELL_IMAGE": IMAGE}
        policy = policy_from_env({**ENV, **images, "SANDBOX_PROXY_USER": "20001:20002"})
        body = _body()
        body["User"] = "20001:20002"

        assert policy.sandbox_user == "20001:20002"
        assert _create(body, policy=policy).allowed
        assert not _create(_body(), policy=policy).allowed
        assert policy_from_env(ENV).sandbox_user == "10001:10001"

    @pytest.mark.parametrize("user", ["0:0", "root", "10001", "0:10001", "10001:0"])
    def test_the_sandbox_user_cannot_be_root(self, user: str) -> None:
        with pytest.raises(ValueError, match="SANDBOX_PROXY_USER"):
            policy_from_env({**ENV, "SANDBOX_PROXY_USER": user})

    def test_no_token_is_an_error(self) -> None:
        """The proxy does not start without a token to ask for."""
        with pytest.raises(ValueError, match="SANDBOX_PROXY_TOKEN"):
            policy_from_env({"DOCKER_HOST_DATA_DIR": "/srv/fim/data"})
        with pytest.raises(ValueError, match="SANDBOX_PROXY_TOKEN"):
            policy_from_env({**ENV, "SANDBOX_PROXY_TOKEN": "short"})

    @pytest.mark.parametrize("network", ["bridge", "host", "none", "default", "container:x"])
    def test_the_egress_network_cannot_be_a_built_in_one(self, network: str) -> None:
        with pytest.raises(ValueError, match="SANDBOX_PROXY_EGRESS_NETWORK"):
            policy_from_env({**ENV, "SANDBOX_PROXY_EGRESS_NETWORK": network})

    def test_the_token_is_created_once_and_kept_private(self, tmp_path: Path) -> None:
        from fim_one.core.tool.sandbox.docker_proxy import load_or_create_token

        path = tmp_path / "secret" / "token"

        first = load_or_create_token(str(path))

        assert len(first) == 64 and load_or_create_token(str(path)) == first
        assert path.stat().st_mode & 0o777 == 0o400


# ---------------------------------------------------------------------------
# Over the wire, against a stand-in daemon
# ---------------------------------------------------------------------------


class _Daemon:
    """Records what reaches the daemon's socket and answers like Docker."""

    def __init__(self) -> None:
        self.requests: list[bytes] = []
        # Answers by method, in turn (the last one is repeated); 201 otherwise.
        self.replies: dict[str, list[int]] = {}
        self.names = {"ab" * 32: "/" + NAME, "cd" * 32: "/fim-one-postgres-1"}
        # The Id a create answers with (``"ab"`` by default: not a full Id).
        self.create_id = "ab"
        # Seconds to wait before answering, by method.
        self.delays: dict[str, float] = {}
        self.networks: dict[str, dict[str, Any]] = {
            "fim-egress": {
                "Name": "fim-egress",
                "Driver": "bridge",
                "Labels": {},
                "Options": {"com.docker.network.bridge.enable_icc": "false"},
            },
        }

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        text = head.decode()
        length = 0
        for line in text.split("\r\n"):
            if line.lower().startswith("content-length:"):
                length = int(line.split(":")[1])
        body = await reader.readexactly(length) if length else b""
        target = text.split(" ")[1]
        lookup = target.endswith("/json") or target.startswith("/networks/")
        if lookup:
            key = target.split("/")[2]
            by_name = {name.lstrip("/"): ident for ident, name in self.names.items()}
            found: Any = (
                self.networks.get(key)
                if target.startswith("/networks/")
                else (
                    {"Name": self.names[key], "Id": key}
                    if key in self.names
                    else ({"Name": "/" + key, "Id": by_name[key]} if key in by_name else None)
                )
            )
            payload = json.dumps(found if found else {"message": "not found"})
            status = "200 OK" if found else "404 Not Found"
            writer.write(
                f"HTTP/1.1 {status}\r\nContent-Length: {len(payload)}\r\n\r\n{payload}".encode()
            )
        else:
            self.requests.append(head + body)
            if "/attach" in target:
                writer.write(
                    b"HTTP/1.1 101 UPGRADED\r\n"
                    b"Content-Type: application/vnd.docker.raw-stream\r\n"
                    b"Connection: Upgrade\r\nUpgrade: tcp\r\n\r\n"
                )
                await writer.drain()
                stdin = await reader.read()  # until the client half-closes
                writer.write(b"echo:" + stdin)
            else:
                await asyncio.sleep(self.delays.get(text.split(" ")[0], 0))
                turns = self.replies.get(text.split(" ")[0])
                if turns:
                    code = turns.pop(0) if len(turns) > 1 else turns[0]
                    writer.write(f"HTTP/1.1 {code} X\r\nContent-Length: 2\r\n\r\n{{}}".encode())
                else:
                    created = json.dumps({"Id": self.create_id})
                    writer.write(
                        f"HTTP/1.1 201 Created\r\nContent-Length: {len(created)}\r\n\r\n"
                        f"{created}".encode()
                    )
        await writer.drain()
        writer.close()


WIRE_POLICY = Policy(
    images=POLICY.images, sandbox_root=ROOT, token=TOKEN, egress_network="fim-egress"
)


@pytest.fixture(autouse=True)
async def _no_lifetime_limits_left() -> AsyncIterator[None]:
    """Lifetime limits a test scheduled end with it (each test has its own loop)."""
    from fim_one.core.tool.sandbox import docker_proxy

    yield
    for task in list(docker_proxy._limits.values()):
        task.cancel()
    docker_proxy._limits.clear()


@pytest.fixture()
async def proxy() -> AsyncIterator[tuple[int, _Daemon]]:
    daemon = _Daemon()
    with tempfile.TemporaryDirectory(prefix="fp", dir="/tmp") as directory:
        path = str(Path(directory) / "docker.sock")
        upstream = await asyncio.start_unix_server(daemon.handle, path)
        server = await serve(WIRE_POLICY, host="127.0.0.1", port=0, docker_socket=path)
        port = server.sockets[0].getsockname()[1]
        try:
            yield port, daemon
        finally:
            server.close()
            upstream.close()
            await server.wait_closed()
            await upstream.wait_closed()


async def _send(port: int, raw: bytes, *, half_close: bool = False) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(raw)
    await writer.drain()
    if half_close:
        writer.write_eof()
    answer = await reader.read()
    writer.close()
    return answer


def _request(
    method: str, target: str, body: bytes = b"", extra: str = "", token: str | None = TOKEN
) -> bytes:
    auth = f"X-Fim-Sandbox-Token: {token}\r\n" if token is not None else ""
    return (
        f"{method} {target} HTTP/1.1\r\nHost: x\r\nContent-Length: {len(body)}\r\n{auth}{extra}\r\n"
    ).encode() + body


class TestOverTheWire:
    async def test_a_sandbox_container_reaches_the_daemon_as_the_proxy_wrote_it(
        self, proxy: tuple[int, _Daemon]
    ) -> None:
        port, daemon = proxy
        body = json.dumps(_body()).encode()

        answer = await _send(
            port, _request("POST", CREATE, body, extra="X-Client-Header: anything\r\n")
        )

        assert answer.startswith(b"HTTP/1.1 201") and len(daemon.requests) == 1
        head, _, sent = daemon.requests[0].partition(b"\r\n\r\n")
        assert head.split(b"\r\n") == [
            f"POST {CREATE} HTTP/1.1".encode(),
            b"Host: docker",
            b"Content-Type: application/json",
            f"Content-Length: {len(sent)}".encode(),
            b"Connection: close",
        ]
        # Not the client's bytes, and neither its headers nor its token.
        assert sent != body and json.loads(sent)["HostConfig"]["Binds"] == [BIND]
        assert b"X-Client-Header" not in head and TOKEN.encode() not in daemon.requests[0]

    @pytest.mark.parametrize("token", [None, "wrong" * 8, TOKEN + "x", ""])
    async def test_without_the_token_nothing_is_forwarded(
        self, proxy: tuple[int, _Daemon], token: str | None
    ) -> None:
        """Every request carries the token."""
        port, daemon = proxy

        answer = await _send(port, _request("GET", "/_ping", token=token))

        assert answer.startswith(b"HTTP/1.1 401") and daemon.requests == []

    async def test_a_host_mount_does_not(self, proxy: tuple[int, _Daemon]) -> None:
        port, daemon = proxy
        body = json.dumps(_body(Binds=["/srv:/mnt"])).encode()

        answer = await _send(port, _request("POST", CREATE, body))

        assert answer.startswith(b"HTTP/1.1 403") and b"sandbox proxy" in answer
        assert daemon.requests == []

    async def test_a_privileged_container_does_not(self, proxy: tuple[int, _Daemon]) -> None:
        port, daemon = proxy
        body = json.dumps(_body(Privileged=True)).encode()

        answer = await _send(port, _request("POST", CREATE, body))

        assert answer.startswith(b"HTTP/1.1 403") and daemon.requests == []

    async def test_a_merged_host_config_does_not(self, proxy: tuple[int, _Daemon]) -> None:
        """A body with a repeated key is refused as a whole."""
        port, daemon = proxy
        good = json.dumps(_body()["HostConfig"] | {"Binds": None})
        body = (
            f'{{"Image":"{IMAGE}","User":"10001:10001",'
            f'"HostConfig":{{"Binds":["/srv:/mnt"]}},"HostConfig":{good}}}'
        ).encode()

        answer = await _send(port, _request("POST", CREATE, body))

        assert answer.startswith(b"HTTP/1.1 403") and daemon.requests == []
        assert not any(b"/srv:/mnt" in request for request in daemon.requests)

    async def test_a_bare_line_feed_in_a_header_is_refused(
        self, proxy: tuple[int, _Daemon]
    ) -> None:
        """A head with a line feed outside a CRLF pair is refused as a whole."""
        port, daemon = proxy
        request = (
            f"GET /_ping HTTP/1.1\r\nHost: x\r\nX-Fim-Sandbox-Token: {TOKEN}\r\n"
            "X-A: a\nX-B: b\r\n\r\n"
        ).encode()

        answer = await _send(port, request)

        assert answer.startswith(b"HTTP/1.1 400")
        assert not any(b"\n\r\n" in request[:-4] for request in daemon.requests)
        assert daemon.requests == []

    async def test_a_second_request_on_the_connection_is_never_forwarded(
        self, proxy: tuple[int, _Daemon]
    ) -> None:
        port, daemon = proxy
        allowed = _request("POST", CREATE, json.dumps(_body()).encode())
        forbidden = _request("POST", CREATE, json.dumps(_body(Binds=["/srv:/mnt"])).encode())

        answer = await _send(port, allowed + forbidden)

        assert answer.startswith(b"HTTP/1.1 201")
        assert len(daemon.requests) == 1 and b"/srv:/mnt" not in daemon.requests[0]

    @pytest.mark.parametrize(
        "extra",
        ["Transfer-Encoding: chunked\r\n", "Content-Length: 0\r\n", " folded: x\r\n"],
        ids=["chunked", "two-lengths", "folded-header"],
    )
    async def test_a_request_that_can_be_read_two_ways_is_refused(
        self, proxy: tuple[int, _Daemon], extra: str
    ) -> None:
        port, daemon = proxy

        answer = await _send(
            port, _request("POST", CREATE, json.dumps(_body()).encode(), extra=extra)
        )

        assert answer.startswith(b"HTTP/1.1 400") and daemon.requests == []

    async def test_a_container_id_is_looked_up(self, proxy: tuple[int, _Daemon]) -> None:
        port, daemon = proxy

        ours = await _send(port, _request("POST", f"/v1.47/containers/{'ab' * 32}/start"))
        theirs = await _send(port, _request("POST", f"/v1.47/containers/{'cd' * 32}/kill"))
        nobody = await _send(port, _request("POST", f"/v1.47/containers/{'ef' * 32}/kill"))

        assert ours.startswith(b"HTTP/1.1 201")
        assert theirs.startswith(b"HTTP/1.1 403") and nobody.startswith(b"HTTP/1.1 403")
        forwarded = [r.split(b" ")[1].decode() for r in daemon.requests]
        assert forwarded == [f"/v1.47/containers/{'ab' * 32}/start"]

    async def test_the_egress_network_needs_the_operators_label(
        self, proxy: tuple[int, _Daemon]
    ) -> None:
        """A network is joined only when the operator marked it as filtered."""
        port, daemon = proxy
        body = json.dumps(_body(NetworkMode="fim-egress")).encode()

        unlabelled = await _send(port, _request("POST", CREATE, body))
        daemon.networks["fim-egress"]["Labels"] = {"fim-one.sandbox-egress": "filtered"}
        labelled = await _send(port, _request("POST", CREATE, body))

        assert unlabelled.startswith(b"HTTP/1.1 403") and b"labelled" in unlabelled
        assert labelled.startswith(b"HTTP/1.1 201") and len(daemon.requests) == 1

    @pytest.mark.parametrize(
        "change",
        [
            {"Containers": {"abc": {"Name": "fim-one-postgres-1"}}},
            {"EnableIPv6": True},
            {"Driver": "overlay"},
            {"Ingress": True},
            {"Options": {}},
            {"Options": {"com.docker.network.bridge.enable_icc": "true"}},
        ],
        ids=["something-else-attached", "ipv6", "not-a-bridge", "ingress", "icc-default", "icc-on"],
    )
    async def test_the_egress_network_holds_sandboxes_only(
        self, proxy: tuple[int, _Daemon], change: dict[str, Any]
    ) -> None:
        port, daemon = proxy
        daemon.networks["fim-egress"]["Labels"] = {"fim-one.sandbox-egress": "filtered"}
        daemon.networks["fim-egress"]["Containers"] = {"x": {"Name": "fim-sandbox-0123abcd"}}
        body = json.dumps(_body(NetworkMode="fim-egress")).encode()

        with_sandboxes_only = await _send(port, _request("POST", CREATE, body))
        daemon.networks["fim-egress"].update(change)
        refused = await _send(port, _request("POST", CREATE, body))

        assert with_sandboxes_only.startswith(b"HTTP/1.1 201")
        assert refused.startswith(b"HTTP/1.1 403") and len(daemon.requests) == 1

    async def test_the_token_is_checked_before_the_body_is_read(
        self, proxy: tuple[int, _Daemon]
    ) -> None:
        """A client that is not known is answered at once; its body is not waited for."""
        port, daemon = proxy
        head = (
            b"POST /containers/create?name=x HTTP/1.1\r\nHost: x\r\n"
            b"X-Fim-Sandbox-Token: wrong\r\nContent-Length: 900000\r\n\r\n"
        )

        answer = await asyncio.wait_for(_send(port, head), timeout=5)

        assert answer.startswith(b"HTTP/1.1 401") and daemon.requests == []

    async def test_the_container_is_always_removed_when_it_exits(
        self, proxy: tuple[int, _Daemon]
    ) -> None:
        port, daemon = proxy
        body = json.dumps(_body(AutoRemove=False)).encode()

        await _send(port, _request("POST", CREATE, body))

        sent = json.loads(daemon.requests[0].partition(b"\r\n\r\n")[2])
        assert sent["HostConfig"]["AutoRemove"] is True

    async def test_no_registry_credentials_are_passed_on(self, proxy: tuple[int, _Daemon]) -> None:
        port, daemon = proxy

        answer = await _send(
            port,
            _request(
                "POST",
                "/v1.47/images/create?fromImage=fim-sandbox&tag=python",
                extra="X-Registry-Auth: e30=\r\n",
            ),
        )

        assert answer.startswith(b"HTTP/1.1 201")
        assert b"Registry-Auth" not in daemon.requests[0]

    async def test_connections_beyond_the_cap_are_turned_away(self) -> None:
        daemon = _Daemon()
        with tempfile.TemporaryDirectory(prefix="fp", dir="/tmp") as directory:
            path = str(Path(directory) / "docker.sock")
            upstream = await asyncio.start_unix_server(daemon.handle, path)
            server = await serve(
                WIRE_POLICY, host="127.0.0.1", port=0, docker_socket=path, max_connections=1
            )
            port = server.sockets[0].getsockname()[1]
            try:
                # Held open: an attach whose client has not finished sending.
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.write(
                    _request(
                        "POST",
                        f"/v1.47/containers/{NAME}/attach?stream=1&stdin=1&stdout=1",
                        extra="Connection: Upgrade\r\nUpgrade: tcp\r\n",
                    )
                )
                await reader.readuntil(b"\r\n\r\n")
                second = await _send(port, _request("GET", "/_ping"))
                writer.close()
            finally:
                server.close()
                upstream.close()

        assert second.startswith(b"HTTP/1.1 503")

    async def test_a_created_container_is_removed_after_its_lifetime(self) -> None:
        """Whatever the client does next, the proxy ends the container."""
        daemon = _Daemon()
        with tempfile.TemporaryDirectory(prefix="fp", dir="/tmp") as directory:
            path = str(Path(directory) / "docker.sock")
            upstream = await asyncio.start_unix_server(daemon.handle, path)
            server = await serve(
                replace(WIRE_POLICY, max_lifetime=0.2),  # type: ignore[arg-type]
                host="127.0.0.1",
                port=0,
                docker_socket=path,
            )
            port = server.sockets[0].getsockname()[1]
            try:
                created = await _send(port, _request("POST", CREATE, json.dumps(_body()).encode()))
                for _ in range(100):
                    if len(daemon.requests) > 1:
                        break
                    await asyncio.sleep(0.05)
            finally:
                server.close()
                upstream.close()

        assert created.startswith(b"HTTP/1.1 201")
        assert daemon.requests[1].startswith(f"DELETE /containers/{NAME}?force=1 ".encode())

    async def _lifetime_run(
        self,
        daemon: _Daemon,
        lifetime: float,
        steps: list[tuple[float, bytes]],
        until: float,
    ) -> list[bytes]:
        """Send *steps* (seconds from the start, request), then wait until *until*.

        The requests that reached the daemon, as their first lines.
        """
        with tempfile.TemporaryDirectory(prefix="fp", dir="/tmp") as directory:
            path = str(Path(directory) / "docker.sock")
            upstream = await asyncio.start_unix_server(daemon.handle, path)
            server = await serve(
                replace(WIRE_POLICY, max_lifetime=lifetime),  # type: ignore[arg-type]
                host="127.0.0.1",
                port=0,
                docker_socket=path,
            )
            port = server.sockets[0].getsockname()[1]
            loop = asyncio.get_running_loop()
            start = loop.time()
            try:
                for at, raw in steps:
                    await asyncio.sleep(max(0.0, start + at - loop.time()))
                    await _send(port, raw)
                await asyncio.sleep(max(0.0, start + until - loop.time()))
            finally:
                server.close()
                upstream.close()
        return [request.split(b"\r\n", 1)[0] for request in daemon.requests]

    async def test_a_failed_create_gets_no_lifetime_limit(self) -> None:
        # 409: the name is taken by a container this create did not make.
        daemon = _Daemon()
        daemon.replies["POST"] = [409]
        create = _request("POST", CREATE, json.dumps(_body()).encode())

        seen = await self._lifetime_run(daemon, 0.2, [(0, create)], until=0.8)

        assert seen == [f"POST /v1.47/containers/create?name={NAME} HTTP/1.1".encode()]

    async def test_a_container_the_client_removed_gets_no_forced_removal(self) -> None:
        daemon = _Daemon()
        daemon.replies["DELETE"] = [204]
        create = _request("POST", CREATE, json.dumps(_body()).encode())
        remove = _request("DELETE", f"/v1.47/containers/{NAME}?force=1")

        seen = await self._lifetime_run(daemon, 1.0, [(0, create), (0.05, remove)], until=1.4)

        assert len(seen) == 2 and seen[1].startswith(b"DELETE /v1.47/")

    async def test_a_name_created_again_has_one_lifetime_limit_from_its_new_create(
        self,
    ) -> None:
        daemon = _Daemon()
        create = _request("POST", CREATE, json.dumps(_body()).encode())

        early = await self._lifetime_run(daemon, 1.0, [(0, create), (0.5, create)], until=1.2)
        late = await self._lifetime_run(_Daemon(), 1.0, [(0, create), (0.5, create)], until=2.3)

        assert not any(line.startswith(b"DELETE") for line in early)
        assert sum(line.startswith(b"DELETE") for line in late) == 1

    async def test_a_removal_of_an_earlier_container_leaves_the_later_ones_limit(
        self,
    ) -> None:
        # The client removes the name after the container it stood for is
        # gone (404); the container created since keeps its limit.
        daemon = _Daemon()
        daemon.create_id = "ef" * 32
        daemon.replies["DELETE"] = [404]
        create = _request("POST", CREATE, json.dumps(_body()).encode())
        remove = _request("DELETE", f"/v1.47/containers/{NAME}?force=1")

        seen = await self._lifetime_run(daemon, 1.0, [(0, create), (0.05, remove)], until=2.0)

        assert seen[2:] == [f"DELETE /containers/{'ef' * 32}?force=1 HTTP/1.1".encode()]

    async def test_removing_the_container_by_name_drops_the_limit_kept_by_its_id(
        self,
    ) -> None:
        daemon = _Daemon()
        daemon.create_id = "ab" * 32  # the stand-in knows it by the sandbox's name
        daemon.replies["DELETE"] = [204]
        create = _request("POST", CREATE, json.dumps(_body()).encode())
        remove = _request("DELETE", f"/v1.47/containers/{NAME}?force=1")

        seen = await self._lifetime_run(daemon, 1.0, [(0, create), (0.05, remove)], until=1.4)

        assert len(seen) == 2 and seen[1].startswith(b"DELETE /v1.47/")

    async def test_a_create_the_daemon_failed_midway_gets_a_limit(self) -> None:
        daemon = _Daemon()
        daemon.replies["POST"] = [500]
        create = _request("POST", CREATE, json.dumps(_body()).encode())

        seen = await self._lifetime_run(daemon, 0.3, [(0, create)], until=1.2)

        assert seen[1:] == [f"DELETE /containers/{NAME}?force=1 HTTP/1.1".encode()]

    async def test_a_create_whose_answer_does_not_come_gets_a_limit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fim_one.core.tool.sandbox import docker_proxy

        monkeypatch.setattr(docker_proxy, "_DAEMON_ANSWER_TIMEOUT", 0.2)
        daemon = _Daemon()
        daemon.delays["POST"] = 1.0
        create = _request("POST", CREATE, json.dumps(_body()).encode())

        seen = await self._lifetime_run(daemon, 0.3, [(0, create)], until=1.5)

        assert f"DELETE /containers/{NAME}?force=1 HTTP/1.1".encode() in seen[1:]

    async def test_a_failed_forced_removal_is_tried_again(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fim_one.core.tool.sandbox import docker_proxy

        monkeypatch.setattr(docker_proxy, "_REMOVAL_RETRY_PAUSES", (0.05, 0.05, 0.05))
        daemon = _Daemon()
        daemon.replies["DELETE"] = [500, 204]
        create = _request("POST", CREATE, json.dumps(_body()).encode())

        seen = await self._lifetime_run(daemon, 0.1, [(0, create)], until=1.2)

        assert seen[1:] == [f"DELETE /containers/{NAME}?force=1 HTTP/1.1".encode()] * 2

    async def test_connections_without_the_token_do_not_take_the_applications_places(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fim_one.core.tool.sandbox import docker_proxy

        monkeypatch.setattr(docker_proxy, "_AUTH_TIMEOUT", 1.5)
        daemon = _Daemon()
        with tempfile.TemporaryDirectory(prefix="fp", dir="/tmp") as directory:
            path = str(Path(directory) / "docker.sock")
            upstream = await asyncio.start_unix_server(daemon.handle, path)
            server = await serve(
                WIRE_POLICY,
                host="127.0.0.1",
                port=0,
                docker_socket=path,
                max_connections=1,
                max_pending=2,
            )
            port = server.sockets[0].getsockname()[1]
            try:
                idle = [await asyncio.open_connection("127.0.0.1", port) for _ in range(2)]
                # Past the grace period in which a pending place is not given up.
                await asyncio.sleep(docker_proxy._EVICT_GRACE + 0.15)
                # The pending places are taken by connections that send
                # nothing: the application's request is still served, at
                # once, and the oldest idle connection gives up its place ...
                served = await asyncio.wait_for(_send(port, _request("GET", "/_ping")), 1)
                evicted = await asyncio.wait_for(idle[0][0].read(), 1)
                # ... while the other one keeps it until its head times out.
                dropped = await asyncio.wait_for(idle[1][0].read(), 5)
                for _, writer in idle:
                    writer.close()
            finally:
                server.close()
                upstream.close()

        assert served.startswith(b"HTTP/1.1 201")
        assert evicted.startswith(b"HTTP/1.1 503") and b"Retry-After: 1" in evicted
        assert dropped.startswith(b"HTTP/1.1 400")

    async def test_a_burst_of_connections_does_not_push_out_a_new_one(self) -> None:
        """Connections opened right after the application's cannot take its place."""
        daemon = _Daemon()
        with tempfile.TemporaryDirectory(prefix="fp", dir="/tmp") as directory:
            path = str(Path(directory) / "docker.sock")
            upstream = await asyncio.start_unix_server(daemon.handle, path)
            server = await serve(
                WIRE_POLICY, host="127.0.0.1", port=0, docker_socket=path, max_pending=16
            )
            port = server.sockets[0].getsockname()[1]
            opened: list[tuple[asyncio.StreamReader, asyncio.StreamWriter]] = []
            try:
                # Pending places taken by connections that send nothing ...
                opened += [await asyncio.open_connection("127.0.0.1", port) for _ in range(16)]
                await asyncio.sleep(0.4)
                # ... then the application connects, and before its head is
                # read a burst arrives.
                app_reader, app_writer = await asyncio.open_connection("127.0.0.1", port)
                await asyncio.sleep(0.02)
                opened += [await asyncio.open_connection("127.0.0.1", port) for _ in range(24)]
                await asyncio.sleep(0.02)
                app_writer.write(_request("GET", "/_ping"))
                await app_writer.drain()
                answer = await asyncio.wait_for(app_reader.read(), 3)
                app_writer.close()
                assert answer.startswith(b"HTTP/1.1 201"), answer[:40]
                # The connections that found no place were answered at once.
                late = await asyncio.wait_for(opened[-1][0].read(), 1)
            finally:
                for _, writer in opened:
                    writer.close()
                server.close()
                upstream.close()

        assert late.startswith(b"HTTP/1.1 503")

    async def test_a_busy_peer_gives_up_its_own_pending_places_first(self) -> None:
        from fim_one.core.tool.sandbox.docker_proxy import _victim

        # Oldest first; peer "b" holds the most places.
        pending = {"t1": "a", "t2": "b", "t3": "b", "t4": "a", "t5": "b"}
        assert _victim(pending) == "t2"
        assert _victim({"t1": "a", "t2": "b"}) == "t1"

    async def test_only_a_connection_past_its_grace_period_gives_up_its_place(self) -> None:
        from fim_one.core.tool.sandbox.docker_proxy import _EVICT_GRACE, _evictable

        now = 100.0
        old, new = now - _EVICT_GRACE - 0.01, now - _EVICT_GRACE / 2
        assert _evictable({"t1": ("a", new), "t2": ("a", new)}, now) is None
        assert _evictable({"t1": ("a", new), "t2": ("b", old)}, now) == "t2"
        # Peers that are not the application go first, whoever holds the most.
        pending = {"t1": ("app", old), "t2": ("app", old), "t3": ("other", old)}
        assert _evictable(pending, now, lambda address: address == "app") == "t3"
        assert _evictable(pending, now) == "t1"

    async def test_attach_streams_both_ways(self, proxy: tuple[int, _Daemon]) -> None:
        port, _ = proxy
        head = _request(
            "POST",
            f"/v1.47/containers/{NAME}/attach?stream=1&stdin=1&stdout=1",
            extra="Connection: Upgrade\r\nUpgrade: tcp\r\n",
        )

        answer = await _send(port, head + b"echo hi\n", half_close=True)

        assert answer.startswith(b"HTTP/1.1 101") and answer.endswith(b"echo:echo hi\n")

    async def test_only_the_application_may_connect(self) -> None:
        daemon = _Daemon()
        with tempfile.TemporaryDirectory(prefix="fp", dir="/tmp") as directory:
            path = str(Path(directory) / "docker.sock")
            upstream = await asyncio.start_unix_server(daemon.handle, path)
            server = await serve(
                WIRE_POLICY,
                host="127.0.0.1",
                port=0,
                docker_socket=path,
                # Resolves to an address that is not this client's.
                client_hosts=["192.0.2.1"],
            )
            port = server.sockets[0].getsockname()[1]
            try:
                answer = await _send(port, _request("GET", "/_ping"))
            finally:
                server.close()
                upstream.close()

        assert answer.startswith(b"HTTP/1.1 403") and daemon.requests == []


def test_the_reference_body_is_not_mutated() -> None:
    body = _body()
    before = copy.deepcopy(body)
    _create(body)
    assert body == before
