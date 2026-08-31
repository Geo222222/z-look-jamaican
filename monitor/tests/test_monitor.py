from pathlib import Path
from tempfile import TemporaryDirectory
import json

from monitor.app.adapters import RepositoryReader, classify, public_wallet

def write(root: Path, rel: str, text: str):
    path=root/rel; path.parent.mkdir(parents=True,exist_ok=True); path.write_text(text,encoding='utf-8')

def test_scan_reads_evidence_and_treasury_without_secrets():
    with TemporaryDirectory() as td:
        root=Path(td)
        write(root,'evidence/events.jsonl','\n'.join([json.dumps({'event_id':'EV-1','type':'evidence','experiment_id':'EXP-MKT-002','timestamp':'2026-08-31T18:00:00Z','summary':'snapshot collected'}),json.dumps({'event_id':'EV-2','type':'wallet','public_address':'0xabc123456789','network':'ethereum-mainnet','asset':'ETH'})]))
        write(root,'secrets/private.json',json.dumps({'private_key':'never-read'}))
        write(root,'config/treasury_destinations.yaml','destinations:\n  - id: treasury-eth\n    asset: ETH\n    network: ethereum-mainnet\n    address: "0x123"\n    status: active\n')
        snap=RepositoryReader(root).scan()
        assert len(snap.records)==2
        assert snap.treasury['destinations'][0]['id']=='treasury-eth'
        assert not any('secrets/private.json'==s.path for s in snap.source_files)
        assert classify(snap.records[0]) in {'evidence','wallet'}

def test_public_wallet_excludes_secret_shaped_record():
    assert public_wallet({'public_address':'0x1234567890','private_key':'bad'}) is None
    out=public_wallet({'public_address':'0x1234567890','network':'ethereum','asset':'ETH','status':'active'})
    assert out and out['asset']=='ETH'
