import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bridge.cn_engine import CampRuntime
from bridge.engine_adapter import cn_profile_enabled, engine_port
from bridge.controller import observation


class ClosedOutput:
    def write(self, _text):
        raise OSError(22, "Invalid argument")

    def flush(self):
        pass


class CampProfileTests(unittest.TestCase):
    def test_camera_projection_changes_do_not_change_the_battle_digest(self):
        state=SimpleNamespace(tick=90,finalized=False,result=None,crowns=(0,0),players=(),entities=(),
                              crop_profile={'id':'cn','world':[151,456,778,1106]})
        initial=observation(state)
        state.crop_profile={'id':'cn','world':[108,400,864,1229]}
        zoomed=observation(state)
        self.assertEqual(initial['digest'],zoomed['digest'])
        self.assertNotEqual(initial['cropProfile'],zoomed['cropProfile'])

    def test_explicit_profile_selects_its_own_default_port(self):
        with patch.dict('os.environ', {'CR_ENGINE_PROFILE':'cn-38.4.5'}, clear=True):
            self.assertTrue(cn_profile_enabled())
            self.assertEqual(engine_port(),26791)
        with patch.dict('os.environ', {}, clear=True):
            self.assertFalse(cn_profile_enabled())
            self.assertEqual(engine_port(),26789)
        with patch.dict('os.environ', {'CR_ENGINE_PROFILE':'unknown'}, clear=True):
            with self.assertRaises(ValueError): cn_profile_enabled()

    def test_start_survives_tauri_retiring_the_stdout_pipe_after_handshake(self):
        profile={'identity':{'apkSha256':'apk','runtimeFingerprintSha256':'runtime'},
                 'cropProfile':{'world':[10,20,30,40]}}
        def adb(*args,**_kwargs):
            if args[:3]==('shell','pm','path'): value='package:/data/app/test/base.apk'
            elif args[:2]==('shell','sha256sum'): value=('apk' if args[2].endswith('base.apk') else 'runtime')+'  file'
            elif args[:2]==('shell','cat'): value=json.dumps({'creator':'offline-research','id':'12345678-1234-1234-1234-123456789abc'})
            elif args[:2]==('shell','pidof'): value='123'
            else:value=''
            return SimpleNamespace(stdout=value)
        research=SimpleNamespace(start=lambda:print('CLI progress'),ready=lambda:print('CLI ready'),
                                 adb=adb,ADB=Path('adb.exe'))
        probe=Mock()
        probe.status.return_value={'ready':True}
        probe.observe.return_value={'mode':'rendered'}
        probe.render_info.return_value={'flipped':False,'physicalSize':[1080,1920],
            'points':[{'world':[0,0],'pixel':[10,20]},{'world':[18000,0],'pixel':[40,20]},
                      {'world':[0,32000],'pixel':[10,60]}]}
        control=SimpleNamespace(NativeControl=Mock(return_value=probe))
        with patch('bridge.cn_engine.load_profile',return_value=profile), \
             patch('bridge.cn_engine.research_modules',return_value=(research,control)), \
             patch('bridge.video.NativeFrameStream') as video:
            runtime=CampRuntime()
            with patch('sys.stdout',ClosedOutput()):
                result=runtime.start()
            self.assertEqual(runtime.stage_code,'ready')
            self.assertTrue(result['capabilities']['exactStep'])
            video.return_value.start.assert_called_once()
            probe.close.assert_called_once()
            runtime.close()


if __name__=='__main__':unittest.main()
