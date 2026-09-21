"""Libmetal firmware API IDs must not be confused with SCMI domain IDs."""
import pytest

from lopper.assists import openamp_xlnx as assist
from lopper.tree import LopperNode, LopperTree


def ttc_tree(scmi_id=25):
    tree = LopperTree()
    tree + LopperNode(-1, "/axi")
    tree['/']['#address-cells'] = [2]
    tree['/']['#size-cells'] = [2]
    transport = LopperNode(-1, '/renamed-transport')
    transport['compatible'] = ['arm,scmi-smc']
    tree + transport
    provider = LopperNode(-1, '/renamed-transport/renamed-power-provider')
    provider['reg'] = [0x11]
    provider['#power-domain-cells'] = [1]
    tree + provider
    provider.phandle_or_create()
    timer = LopperNode(-1, '/timer@f1e90000')
    timer['reg'] = [0, 0xf1e90000, 0, 0x1000]
    timer['power-domains'] = [provider.phandle, scmi_id]
    tree + timer
    return tree, timer, provider


@pytest.mark.parametrize('scmi_id,expected', [
    (22, 0x18224024), (23, 0x18224025), (24, 0x18224026), (25, 0x18224027),
    (58, 0x1822411f), (59, 0x18224120), (60, 0x18224121), (61, 0x18224122),
])
def test_scmi_ttc_ids(scmi_id, expected):
    tree, timer, provider = ttc_tree(scmi_id)
    assert assist._libmetal_ttc_xilpm_node_id(tree, timer, assist.SOC_TYPE.VERSAL2) == expected
    assert timer['power-domains'].value == [provider.phandle, scmi_id]


@pytest.mark.parametrize('platform,compatible,node_id', [
    (assist.SOC_TYPE.ZYNQMP, 'xlnx,zynqmp-firmware', 0x1a),
    (assist.SOC_TYPE.VERSAL, 'xlnx,versal-firmware', 0x18224027),
    (assist.SOC_TYPE.VERSAL_NET, 'xlnx,versal-net-firmware', 0x18224027),
    (assist.SOC_TYPE.VERSAL2, 'xlnx,versal2-firmware', 0x18224027),
])
def test_direct_firmware_id_preserved(platform, compatible, node_id):
    tree, timer, provider = ttc_tree(node_id)
    provider['compatible'] = [compatible]
    assert assist._libmetal_ttc_xilpm_node_id(tree, timer, platform) == node_id


@pytest.mark.parametrize('problem,diagnostic', [
    ('missing', 'expected one power-domains'),
    ('short', 'expected one power-domains'),
    ('multiple', 'expected one power-domains'),
    ('unresolved', 'unresolved power-domains provider'),
    ('cells', '#power-domain-cells'),
    ('unknown_id', 'unsupported Versal2 SCMI TTC power-domain ID'),
    ('firmware_id_under_scmi', 'unsupported Versal2 SCMI TTC power-domain ID'),
    ('transport', 'unsupported TTC power-domains provider'),
    ('protocol', 'unsupported TTC power-domains provider'),
    ('platform', 'unsupported TTC power-domains provider'),
])
def test_invalid_ttc_binding_reports_timer(problem, diagnostic):
    tree, timer, provider = ttc_tree()
    platform = assist.SOC_TYPE.VERSAL2
    if problem == 'missing':
        timer.delete('power-domains')
    elif problem == 'short':
        timer['power-domains'] = [provider.phandle]
    elif problem == 'multiple':
        timer['power-domains'] = [provider.phandle, 25, provider.phandle, 24]
    elif problem == 'unresolved':
        timer['power-domains'] = [0xdead, 25]
    elif problem == 'cells':
        provider['#power-domain-cells'] = [2]
    elif problem == 'unknown_id':
        timer['power-domains'] = [provider.phandle, 0xffff]
    elif problem == 'firmware_id_under_scmi':
        timer['power-domains'] = [provider.phandle, 0x18224027]
    elif problem == 'transport':
        provider.parent['compatible'] = ['vendor,unknown']
    elif problem == 'protocol':
        provider['reg'] = [0x14]
    elif problem == 'platform':
        platform = assist.SOC_TYPE.VERSAL
    with pytest.raises(ValueError, match=diagnostic) as exc:
        assist._libmetal_ttc_xilpm_node_id(tree, timer, platform)
    assert timer.abs_path in str(exc.value)


@pytest.mark.parametrize('os_name', ['linux_dt', 'baremetal_dt'])
def test_cmake_uses_xilpm_id_and_preserves_scmi_binding(tmp_path, monkeypatch, os_name):
    tree, timer, provider = ttc_tree()
    carveouts = []
    for i, name in enumerate(('desc0', 'desc1', 'data')):
        base = 0x100000 + i * 0x10000
        node = LopperNode(-1, f'/{name}@{base:x}')
        node['reg'] = [0, base, 0, 0x10000]
        tree + node
        carveouts.append(node)
    mailbox = LopperNode(-1, '/mailbox@eb330000')
    mailbox['reg'] = [0, 0xeb330000, 0, 0x1000]
    mailbox['xlnx,int-id'] = [64]
    tree + mailbox
    channel = LopperNode(-1, '/mailbox@eb330000/channel')
    channel['xlnx,ipi-bitmask'] = [8]
    tree + channel
    monkeypatch.setattr(assist, 'get_platform', lambda *args: assist.SOC_TYPE.VERSAL2)
    output = tmp_path / 'libmetal.cmake'
    assert assist.xlnx_libmetal_gen_output_file(
        tree, output, carveouts, channel, timer, os_name)
    import re
    values = dict(re.findall(r'set\((\w+)\s+([^\s)]+)\)', output.read_text()))
    assert values['TTC_NODEID'] == '0x18224027'
    assert values['TTC_BASE_ADDR'] == '0xf1e90000'
    assert timer['power-domains'].value == [provider.phandle, 25]
