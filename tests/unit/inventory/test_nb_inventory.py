# -*- coding: utf-8 -*-
# Copyright: (c) 2020, Hillsong, Douglas Heriot (@DouglasHeriot) <douglas.heriot@hillsong.com>
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

from functools import partial
from pathlib import Path
from unittest.mock import Mock, call, mock_open, patch

import pytest
from packaging import version

try:
    from ansible_collections.netbox.netbox.plugins.inventory.nb_inventory import (
        InventoryModule,
    )
    from ansible_collections.netbox.netbox.tests.unit.helpers.load_data import (
        load_test_data,
    )

except ImportError:
    import sys

    # Not installed as a collection
    # Try importing relative to root directory of this ansible_modules project

    sys.path.append("plugins/inventory")
    sys.path.append("tests")
    from tests.unit.helpers.load_data import load_test_data

load_relative_test_data = partial(load_test_data, Path(__file__).resolve().parent)


class MockInventory:
    def __init__(self):
        self.variables = {}
        self.groups = set()
        self.children = {}
        self.hosts = {}

    def set_variable(self, hostname, key, value):
        if hostname not in self.variables:
            self.variables[hostname] = {}

        self.variables[hostname][key] = value

    def add_group(self, group):
        self.groups.add(group)
        return group

    def add_child(self, parent, child):
        self.children.setdefault(parent, set()).add(child)

    def add_host(self, group, host):
        self.hosts.setdefault(group, set()).add(host)


@pytest.fixture
def inventory_fixture(
    allowed_device_query_parameters_fixture, allowed_vm_query_parameters_fixture
):
    inventory = InventoryModule()
    inventory.api_endpoint = "https://netbox.test.endpoint:1234"

    # Fill in data that is fetched dynamically
    inventory.api_version = version.Version("2.0")
    inventory.allowed_device_query_parameters = allowed_device_query_parameters_fixture
    inventory.allowed_vm_query_parameters = allowed_vm_query_parameters_fixture

    # Inventory mock, to validate what has been set via inventory.inventory.set_variable
    inventory.inventory = MockInventory()

    return inventory


@pytest.fixture
def templar_fixture():
    templar = Mock()

    return templar


@pytest.fixture
def allowed_device_query_parameters_fixture():
    # Subset of parameters - real list is fetched dynamically from NetBox openapi endpoint
    return [
        "id",
        "interfaces",
        "has_primary_ip",
        "mac_address",
        "name",
        "platform",
        "rack_id",
        "region",
        "role",
        "tag",
    ]


@pytest.fixture
def allowed_vm_query_parameters_fixture():
    # Subset of parameters - real list is fetched dynamically from NetBox openapi endpoint
    return [
        "id",
        "virtual_disks",
        "interfaces",
        "disk",
        "mac_address",
        "name",
        "platform",
        "region",
        "role",
        "tag",
    ]


@pytest.mark.parametrize(
    "parameter, expected", load_relative_test_data("validate_query_parameter")
)
def test_validate_query_parameter(inventory_fixture, parameter, expected):
    value = "some value, doesn't matter"
    result = inventory_fixture.validate_query_parameter(
        {parameter: value}, inventory_fixture.allowed_device_query_parameters
    )
    assert (result == (parameter, value)) == expected


@pytest.mark.parametrize(
    "parameters, expected", load_relative_test_data("filter_query_parameters")
)
def test_filter_query_parameters(inventory_fixture, parameters, expected):
    result = inventory_fixture.filter_query_parameters(
        parameters, inventory_fixture.allowed_device_query_parameters
    )

    # Result is iterators of tuples
    # expected from json file is an array of dicts

    # Convert result iterator to list so we can get the length, and iterate over with an index
    result_list = list(result)

    assert len(result_list) == len(expected)

    for i, parameter in enumerate(result_list):
        assert parameter[0] == list(expected[i].keys())[0]
        assert parameter[1] == list(expected[i].values())[0]


@pytest.mark.parametrize("options, expected", load_relative_test_data("refresh_url"))
def test_refresh_url(inventory_fixture, options, expected):
    inventory_fixture.query_filters = options["query_filters"]
    inventory_fixture.device_query_filters = options["device_query_filters"]
    inventory_fixture.vm_query_filters = options["vm_query_filters"]
    inventory_fixture.config_context = options["config_context"]

    result = inventory_fixture.refresh_url()

    assert result == tuple(expected)


def test_refresh_lookups(inventory_fixture):
    def raises_exception():
        raise Exception("Error from within a thread")

    def does_not_raise():
        pass

    with pytest.raises(Exception) as e:
        inventory_fixture.refresh_lookups([does_not_raise, raises_exception])
    assert "Error from within a thread" in str(e)

    inventory_fixture.refresh_lookups([does_not_raise, does_not_raise])


@pytest.mark.parametrize(
    "plurals, services, virtual_disks, interfaces, dns_name, ansible_host_dns_name, racks, expected, not_expected",
    load_relative_test_data("group_extractors"),
)
def test_group_extractors(
    inventory_fixture,
    plurals,
    services,
    virtual_disks,
    interfaces,
    dns_name,
    ansible_host_dns_name,
    racks,
    expected,
    not_expected,
):
    inventory_fixture.plurals = plurals
    inventory_fixture.services = services
    inventory_fixture.virtual_disks = virtual_disks
    inventory_fixture.interfaces = interfaces
    inventory_fixture.dns_name = dns_name
    inventory_fixture.ansible_host_dns_name = ansible_host_dns_name
    inventory_fixture.racks = racks
    extractors = inventory_fixture.group_extractors

    for key in expected:
        assert key in extractors

    for key in not_expected:
        assert key not in expected


def test_refresh_device_roles_lookup_parent(inventory_fixture):
    # NetBox 4.3+ device roles may have a "parent" role, forming a hierarchy.
    # Older NetBox versions don't return a "parent" key at all.
    device_roles = [
        {"id": 1, "slug": "router", "name": "Router"},
        {"id": 2, "slug": "access-router", "name": "Access Router", "parent": {"id": 1}},
        {"id": 3, "slug": "border-router", "name": "Border Router", "parent": {"id": 1}},
        {"id": 4, "slug": "switch", "name": "Switch", "parent": None},
    ]

    inventory_fixture.get_resource_list = Mock(return_value=device_roles)
    inventory_fixture.refresh_device_roles_lookup()

    assert inventory_fixture.device_roles_lookup == {
        1: "router",
        2: "access-router",
        3: "border-router",
        4: "switch",
    }
    assert inventory_fixture.device_role_parent_lookup == {
        1: None,
        2: 1,
        3: 1,
        4: None,
    }


def test_add_device_role_groups_nests_by_parent(inventory_fixture):
    inventory_fixture.plurals = True
    inventory_fixture.group_names_raw = False
    inventory_fixture.device_roles_lookup = {
        1: "router",
        2: "access-router",
        3: "border-router",
    }
    inventory_fixture.device_role_parent_lookup = {1: None, 2: 1, 3: 1}

    inventory_fixture._add_device_role_groups()

    assert inventory_fixture.inventory.groups == {
        "device_roles_router",
        "device_roles_access-router",
        "device_roles_border-router",
    }
    assert inventory_fixture.inventory.children["device_roles_router"] == {
        "device_roles_access-router",
        "device_roles_border-router",
    }

    # A host with the leaf role "access-router" should be filed under that
    # group; nesting makes it transitively part of "device_roles_router" too.
    inventory_fixture.group_by = ["device_roles"]
    inventory_fixture.racks = False
    inventory_fixture.services = False
    inventory_fixture.virtual_disks = False
    inventory_fixture.interfaces = False
    inventory_fixture.dns_name = False
    inventory_fixture.ansible_host_dns_name = False

    inventory_fixture.add_host_to_groups(
        host={"id": 100, "role": {"id": 2}}, hostname="router1"
    )

    assert inventory_fixture.inventory.hosts == {
        "device_roles_access-router": {"router1"}
    }


def test_refresh_platforms_lookup_parent(inventory_fixture):
    # NetBox 4.4+ platforms may have a "parent" platform, forming a hierarchy.
    # Older NetBox versions don't return a "parent" key at all.
    platforms = [
        {"id": 1, "slug": "linux", "name": "Linux"},
        {"id": 2, "slug": "debian", "name": "Debian", "parent": {"id": 1}},
        {"id": 3, "slug": "rhel", "name": "RHEL", "parent": {"id": 1}},
        {"id": 4, "slug": "windows", "name": "Windows", "parent": None},
    ]

    inventory_fixture.get_resource_list = Mock(return_value=platforms)
    inventory_fixture.refresh_platforms_lookup()

    assert inventory_fixture.platforms_lookup == {
        1: "linux",
        2: "debian",
        3: "rhel",
        4: "windows",
    }
    assert inventory_fixture.platform_parent_lookup == {
        1: None,
        2: 1,
        3: 1,
        4: None,
    }


def test_add_platform_groups_nests_by_parent(inventory_fixture):
    inventory_fixture.plurals = True
    inventory_fixture.group_names_raw = False
    inventory_fixture.platforms_lookup = {
        1: "linux",
        2: "debian",
        3: "rhel",
    }
    inventory_fixture.platform_parent_lookup = {1: None, 2: 1, 3: 1}

    inventory_fixture._add_platform_groups()

    assert inventory_fixture.inventory.groups == {
        "platforms_linux",
        "platforms_debian",
        "platforms_rhel",
    }
    assert inventory_fixture.inventory.children["platforms_linux"] == {
        "platforms_debian",
        "platforms_rhel",
    }

    # A host with the leaf platform "debian" should be filed under that
    # group; nesting makes it transitively part of "platforms_linux" too.
    inventory_fixture.group_by = ["platforms"]
    inventory_fixture.racks = False
    inventory_fixture.services = False
    inventory_fixture.virtual_disks = False
    inventory_fixture.interfaces = False
    inventory_fixture.dns_name = False
    inventory_fixture.ansible_host_dns_name = False

    inventory_fixture.add_host_to_groups(
        host={"id": 100, "platform": {"id": 2}}, hostname="server1"
    )

    assert inventory_fixture.inventory.hosts == {"platforms_debian": {"server1"}}


def test_group_extractors_includes_prefix_when_requested(inventory_fixture):
    inventory_fixture.plurals = True
    inventory_fixture.services = False
    inventory_fixture.virtual_disks = False
    inventory_fixture.interfaces = False
    inventory_fixture.dns_name = False
    inventory_fixture.ansible_host_dns_name = False
    inventory_fixture.racks = False

    inventory_fixture.group_by = []
    assert "prefixes" not in inventory_fixture.group_extractors

    inventory_fixture.group_by = ["prefixes"]
    assert "prefixes" in inventory_fixture.group_extractors


def test_refresh_prefixes_lookup_parent(inventory_fixture):
    # NetBox prefixes have no explicit "parent" field (unlike device roles or
    # platforms), so containment - and therefore the grouping hierarchy - has
    # to be computed directly from the CIDRs.
    prefixes = [
        {"id": 1, "prefix": "10.0.0.0/8"},
        {"id": 2, "prefix": "10.0.0.0/16"},
        {"id": 3, "prefix": "10.0.10.0/24"},
        {"id": 4, "prefix": "192.168.1.0/24"},
    ]

    inventory_fixture.get_resource_list = Mock(return_value=prefixes)
    inventory_fixture.refresh_prefixes_lookup()

    assert inventory_fixture.prefixes_lookup == {
        1: "10_0_0_0_8",
        2: "10_0_0_0_16",
        3: "10_0_10_0_24",
        4: "192_168_1_0_24",
    }
    assert inventory_fixture.prefix_parent_lookup == {
        1: None,
        2: 1,
        3: 2,
        4: None,
    }


def test_extract_prefix_matches_most_specific(inventory_fixture):
    inventory_fixture.interfaces = False
    inventory_fixture.get_resource_list = Mock(
        return_value=[
            {"id": 1, "prefix": "10.0.0.0/8"},
            {"id": 2, "prefix": "10.0.0.0/16"},
            {"id": 3, "prefix": "10.0.10.0/24"},
        ]
    )
    inventory_fixture.refresh_prefixes_lookup()

    host = {"primary_ip4": {"address": "10.0.10.5/24"}}
    assert inventory_fixture.extract_prefix(host) == ["10_0_10_0_24"]

    # An address outside of any known prefix should not match anything
    assert (
        inventory_fixture.extract_prefix({"primary_ip4": {"address": "172.16.0.1/24"}})
        == []
    )

    # A host without a primary IP should not match anything either
    assert inventory_fixture.extract_prefix({}) == []


def test_extract_prefix_dual_stack_matches_both_families(inventory_fixture):
    # A host with both a primary IPv4 and a primary IPv6 address should be
    # grouped by both - not just the IPv4 one (NetBox's own "primary_ip"
    # property prefers IPv4 whenever both are set, which would otherwise
    # silently hide the IPv6 prefix membership).
    inventory_fixture.interfaces = False
    inventory_fixture.get_resource_list = Mock(
        return_value=[
            {"id": 1, "prefix": "10.0.10.0/24"},
            {"id": 2, "prefix": "2001:db8::/32"},
        ]
    )
    inventory_fixture.refresh_prefixes_lookup()

    host = {
        "primary_ip4": {"address": "10.0.10.5/24"},
        "primary_ip6": {"address": "2001:db8::5/32"},
    }

    assert set(inventory_fixture.extract_prefix(host)) == {
        "10_0_10_0_24",
        "2001_db8___32",
    }


def test_extract_prefix_checks_all_interface_addresses(inventory_fixture):
    # A single interface (or several) may carry more than one IPv4 address at
    # once (secondary addresses, VIPs, etc). When "interfaces" is enabled,
    # all of them should be checked, not just the device's primary address.
    inventory_fixture.interfaces = True
    inventory_fixture.get_resource_list = Mock(
        return_value=[
            {"id": 1, "prefix": "10.0.10.0/24"},
            {"id": 2, "prefix": "10.0.20.0/24"},
        ]
    )
    inventory_fixture.refresh_prefixes_lookup()

    inventory_fixture.extract_interfaces = Mock(
        return_value=[
            {
                "name": "eth0",
                "ip_addresses": [
                    {"address": "10.0.10.5/24"},
                    {"address": "10.0.20.5/24"},
                ],
            }
        ]
    )

    host = {"primary_ip4": {"address": "10.0.10.5/24"}}

    assert set(inventory_fixture.extract_prefix(host)) == {
        "10_0_10_0_24",
        "10_0_20_0_24",
    }


def test_add_prefix_groups_nests_by_containment(inventory_fixture):
    inventory_fixture.plurals = True
    inventory_fixture.group_names_raw = False
    inventory_fixture.get_resource_list = Mock(
        return_value=[
            {"id": 1, "prefix": "10.0.0.0/8"},
            {"id": 2, "prefix": "10.0.0.0/16"},
            {"id": 3, "prefix": "10.0.10.0/24"},
        ]
    )
    inventory_fixture.refresh_prefixes_lookup()

    inventory_fixture._add_prefix_groups()

    assert inventory_fixture.inventory.groups == {
        "prefixes_10_0_0_0_8",
        "prefixes_10_0_0_0_16",
        "prefixes_10_0_10_0_24",
    }
    assert inventory_fixture.inventory.children["prefixes_10_0_0_0_8"] == {
        "prefixes_10_0_0_0_16",
    }
    assert inventory_fixture.inventory.children["prefixes_10_0_0_0_16"] == {
        "prefixes_10_0_10_0_24",
    }

    # A host whose primary IP falls in the most specific prefix should be
    # filed under that leaf group; nesting makes it transitively part of
    # "prefixes_10_0_0_0_16" and "prefixes_10_0_0_0_8" too.
    inventory_fixture.group_by = ["prefixes"]
    inventory_fixture.racks = False
    inventory_fixture.services = False
    inventory_fixture.virtual_disks = False
    inventory_fixture.interfaces = False
    inventory_fixture.dns_name = False
    inventory_fixture.ansible_host_dns_name = False

    inventory_fixture.add_host_to_groups(
        host={"id": 100, "primary_ip4": {"address": "10.0.10.5/24"}},
        hostname="server1",
    )

    assert inventory_fixture.inventory.hosts == {"prefixes_10_0_10_0_24": {"server1"}}


@pytest.mark.parametrize(
    "api_url, max_uri_length, query_key, query_values, expected",
    load_relative_test_data("get_resource_list_chunked"),
)
def test_get_resource_list_chunked(
    inventory_fixture, api_url, max_uri_length, query_key, query_values, expected
):
    mock_get_resource_list = Mock()
    mock_get_resource_list.return_value = ["resource"]

    inventory_fixture.get_resource_list = mock_get_resource_list
    inventory_fixture.max_uri_length = max_uri_length

    resources = inventory_fixture.get_resource_list_chunked(
        api_url, query_key, query_values
    )

    mock_get_resource_list.assert_has_calls(map(call, expected))
    assert mock_get_resource_list.call_count == len(expected)
    assert resources == mock_get_resource_list.return_value * len(expected)


@patch(
    "ansible_collections.netbox.netbox.plugins.inventory.nb_inventory.DEFAULT_LOCAL_TMP",
    "/fake/path/asdasd3456",
)
@pytest.mark.parametrize("netbox_ver", ["2.0.2", "3.0.0"])
def test_fetch_api_docs(inventory_fixture, netbox_ver):
    mock_fetch_information = Mock()
    mock_fetch_information.side_effect = [
        {"netbox-version": netbox_ver},
        {"info": {"version": "3.0"}},
    ]

    inventory_fixture._fetch_information = mock_fetch_information

    with pytest.raises(KeyError, match="paths"):
        with patch("builtins.open", mock_open()) as filemock:
            with patch(
                "ansible_collections.netbox.netbox.plugins.inventory.nb_inventory.json"
            ) as json_mock:
                json_mock.load.return_value = {"info": {"version": "2.0"}}
                inventory_fixture.fetch_api_docs()

    ref_args_list = [call("/fake/path/netbox_api_dump.json")]
    if netbox_ver == "3.0.0":
        ref_args_list.append(call("/fake/path/netbox_api_dump.json", "w"))

    assert filemock.call_args_list == ref_args_list
    assert str(inventory_fixture.api_version) == netbox_ver[:-2]


def test_new_token(inventory_fixture, templar_fixture):
    mock_get_option = Mock()

    mock_templar_template_token = Mock()
    mock_templar_template_token.return_value = {"type": "foo", "value": "bar"}

    inventory_fixture.templar = templar_fixture
    inventory_fixture.templar.template = mock_templar_template_token

    inventory_fixture.get_option = mock_get_option

    inventory_fixture.headers = {}

    inventory_fixture._set_authorization()

    assert "Authorization" in inventory_fixture.headers
    assert inventory_fixture.headers["Authorization"] == "Foo bar"


@pytest.mark.parametrize(
    "custom_fields, expected", load_relative_test_data("extract_custom_fields")
)
def test_extract_custom_fields(inventory_fixture, custom_fields, expected):
    extracted_custom_fields = inventory_fixture.extract_custom_fields(
        {"custom_fields": custom_fields}
    )

    assert extracted_custom_fields == expected


def test_rename_variables(inventory_fixture):
    inventory_fixture.rename_variables = inventory_fixture.parse_rename_variables(
        (
            {"pattern": r"cluster(.*)", "repl": r"netbox_cluster\1"},
            {"pattern": r"ansible_host", "repl": r"host"},
        )
    )

    inventory_fixture._set_variable("host", "ansible_fqdn", "host.example.org")
    inventory_fixture._set_variable("host", "ansible_host", "host")
    inventory_fixture._set_variable("host", "cluster", "staging")
    inventory_fixture._set_variable("host", "cluster_id", "0xdeadbeef")

    assert inventory_fixture.inventory.variables["host"] == {
        "ansible_fqdn": "host.example.org",
        "host": "host",
        "netbox_cluster": "staging",
        "netbox_cluster_id": "0xdeadbeef",
    }
