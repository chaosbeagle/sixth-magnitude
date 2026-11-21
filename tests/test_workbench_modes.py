import io
import sys

import pytest
from PIL import Image
import numpy as np

from pathology_tools import free_session, reviewed_hybrid
from pathology_tools.free_edit import Canvas
from pathology_tools.packets import prepare
from sixth_magnitude.hybrid_entry import main as astra
from sols_hammer.hybrid_entry import main as sol


@pytest.mark.parametrize(('entry','host','individual'), [(astra,'astra',True), (sol,'sol',False)])
@pytest.mark.parametrize(('label','slug','engine'), [
    ('工具導引','tool-guided','tools'), ('模型導引','model-guided','raw'), ('混合','hybrid','hybrid')
])
def test_named_choice_reaches_editing_engine(tmp_path,monkeypatch,entry,host,individual,label,slug,engine):
    field=tmp_path/'H01'
    rgb=np.full((40,40,3),230,np.uint8)
    rgb[9:23,10:24]=[80,30,95]
    if engine=='raw':
        field.mkdir()
        Image.fromarray(rgb).save(field/'source.png')
    else:
        prepare(rgb,[0,0],'H01',field,native=True)
    observed=[]
    def check_launch(actual_host,actual_individual,args):
        assert (actual_host,actual_individual,args.mode)==(host,individual,engine)
        canvas=Canvas(args.fields[0],args.mode,None,tmp_path,tmp_path,args.classes)
        assert bool(canvas.bank)==(engine!='raw')
        canvas.view()
        if engine=='tools':
            with pytest.raises(ValueError):
                canvas.apply(dict(action='paint',strokes=[dict(id=1,points=[[1,1],[3,1],[3,3]])]))
        else:
            canvas.apply(dict(action='paint',strokes=[dict(id=1,points=[[1,1],[3,1],[3,3]])]))
            assert canvas.pending[2,2]==1
        observed.append(args.mode)
    monkeypatch.setattr(reviewed_hybrid if engine == 'hybrid' else free_session,'run',check_launch)
    for choice in {label,slug,engine}:
        monkeypatch.setattr(sys,'argv',['host','workbench','--mode',choice,'--fields',str(field),'--output',str(tmp_path/'result')])
        entry()
    assert len(observed)==len({label,slug,engine})


def test_interactive_menu_accepts_number_and_paths_with_spaces(tmp_path,monkeypatch,capsys):
    folder=tmp_path/'first field';folder.mkdir()
    Image.new('RGB',(5,5)).save(folder/'source.png')
    stream=io.StringIO(f'wrong\n2\n{folder}\n\n{tmp_path}/result\n')
    monkeypatch.setattr(stream,'isatty',lambda:True)
    monkeypatch.setattr(sys,'stdin',stream)
    monkeypatch.setattr(sys,'argv',['sols-hammer','workbench'])
    received=[]
    monkeypatch.setattr(free_session,'run',lambda h,i,a:received.append((a.mode,a.fields,a.output)))
    sol()
    assert received==[('raw',[folder],tmp_path/'result')]
    display=capsys.readouterr().err
    for label in ['工具導引','模型導引','混合']:
        assert label in display


def test_unattended_missing_choice_never_launches(tmp_path,monkeypatch,capsys):
    monkeypatch.setattr(sys,'stdin',io.StringIO(''))
    monkeypatch.setattr(sys,'argv',['sixth-magnitude','workbench','--fields','H01','--output',str(tmp_path/'result')])
    with pytest.raises(SystemExit) as error:
        astra()
    assert error.value.code==2 and '--mode' in capsys.readouterr().err
    assert not (tmp_path/'result').exists()
