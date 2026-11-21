import numpy as np
import pytest
from PIL import Image
from pathology_tools.free_edit import Canvas, pixel_edit, Review

def test_local_edits_preserve_external_ids_and_zero_can_erase():
    base=np.arange(25,dtype=np.int32).reshape(5,5)
    roi=[1,1,2,2]
    got=pixel_edit(base,roi,array=np.array([[0,91],[91,0]],np.int32))
    want=base.copy();want[1:3,1:3]=[[0,91],[91,0]]
    assert np.array_equal(got,want)
    cut=pixel_edit(got,[],strokes=[dict(id=0,points=[[1,1],[2,1],[1,2]])])
    assert cut[1,2]==cut[2,1]==0 and cut[4,4]==24
    with pytest.raises(ValueError):pixel_edit(base,roi,array=np.zeros((1,1),'i4'))
    with pytest.raises(ValueError):pixel_edit(base,roi,strokes=[dict(id=2,points=[[0,0],[1,2],[2,2]])])

def test_raw_transactions_and_type_coverage(tmp_path):
    src=tmp_path/'H';src.mkdir();work=tmp_path/'work';work.mkdir();out=tmp_path/'out';out.mkdir()
    Image.new('RGB',(12,12),'pink').save(src/'source.png')
    bench=Review([src],'raw',None,work,out,['A','B'],True)
    f=bench.fields['H'];bench.handle([])
    assert not f.bank
    proposal=dict(field='H',action='paint',strokes=[dict(id=11,points=[[1,1],[9,1],[9,8],[1,8]])])
    bench.handle([proposal]);assert f.pending.max()==11 and not f.current.any()
    bench.handle([dict(field='H',action='undo')]);assert f.pending is None and not f.current.any()
    bench.handle([proposal,dict(field='H',action='accept')]);assert f.current.max()==11
    assert 'failed' in bench.handle([dict(field='H',action='seal',default=0)])[0]['text']
    bench.handle([dict(field='H',action='seal',default=2)])
    with np.load(out/'H.npz') as result:
        assert result['ids'].tolist()==[11] and result['types'].tolist()==[2]
    assert not bench.active()

def test_mixing_tool_and_pixels_with_tool_only_guard(tmp_path):
    from pathology_tools.packets import prepare
    rgb=np.full((48,48,3),235,np.uint8)
    rgb[12:25,12:25]=[50,20,85]
    path=tmp_path/'packet';prepare(rgb,[0,0],'packet',path,native=True)
    for mode in ('hybrid','tools'):
        work=tmp_path/(mode+'-work');work.mkdir();out=tmp_path/mode;out.mkdir()
        f=Canvas(path,mode,None,work,out,['nucleus']);f.view()
        f.apply(dict(action='whole',donor='local_gradient'));f.apply(dict(action='accept'))
        original=f.current.copy();patch=np.array([[41,41],[41,0]],np.int32)
        np.save(work/'patch.npy',patch);edit=dict(action='pixels',roi=[3,3,2,2],file='patch.npy')
        if mode=='tools':
            with pytest.raises(ValueError):f.apply(edit)
            assert np.array_equal(f.current,original)
        else:
            f.apply(edit);assert np.array_equal(f.current,original)
            expected=original.copy();expected[3:5,3:5]=patch
            f.apply(dict(action='accept'));assert np.array_equal(f.current,expected)
            f.apply(dict(action='whole',donor='baseline'));f.apply(dict(action='undo'))
            f.apply(dict(action='seal',default=1))
            assert np.array_equal(np.load(out/'packet.npz')['labels'],expected)
