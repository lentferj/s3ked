

def test_the_demo_load_source_matches_the_bridge_exactly():
    """The stand-in must not be more generous than the machine.

    DemoBridge returned a "volume" key S3kBridge has never returned -- there
    is no volume register (§72). The application read it with a default, so
    it showed `vol 001` against the demo and `vol 000` against hardware, and
    the tests agreed with the demo for as long as the bug existed. A demo
    that answers a question the device refuses is worse than no demo.
    """
    import inspect
    from s3k import bridge as b
    from s3ked.demo import DemoBridge

    source = inspect.getsource(b.S3kBridge.load_source)
    real_keys = {
        line.split('"')[1]
        for line in source.splitlines()
        if line.strip().startswith('"') and '":' in line
    }
    assert real_keys, "could not read the bridge's own key list"
    assert set(DemoBridge().load_source()) == real_keys
