"""Global test fixtures — mock astrbot + create data dir"""
import os
import sys
import types

os.makedirs("data", exist_ok=True)

# Create proper module hierarchy (not MagicMock!)
for name in ['astrbot', 'astrbot.api', 'astrbot.api.event']:
    m = types.ModuleType(name)
    m.__path__ = []
    sys.modules[name] = m

# Message components — Plain() returns its argument
class _FakeMC:
    @staticmethod
    def Plain(text, *a, **kw): return text
    Image = None
    Node = None

sys.modules['astrbot.api.message_components'] = _FakeMC()
sys.modules['astrbot.api.event'].MessageChain = lambda *a, **kw: None
