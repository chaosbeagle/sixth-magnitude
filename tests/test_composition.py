import numpy as np
import pytest
from pathology_tools.composition import compose, centers, seeded, delta, walker


def test_complete_objects_and_conflicts():
    base = np.array([[1,1,2],[0,0,2]], dtype=np.int32)
    donor = np.array([[6,7,7],[0,0,0]])
    with pytest.raises(ValueError, match="retained IDs"):
        compose(base, donor, [1], [6,7], [])
    with pytest.raises(ValueError, match="cross"):
        compose(base, donor, [1,2], [6,7], [0,0,2,2])
    output, scope = compose(base, donor, [1], [6], [0,0,2,1])
    assert output.tolist() == [[3,0,2],[0,0,2]]
    assert delta(base, output, scope)["outside_unchanged"] == 4
    assert base.tolist() == [[1,1,2],[0,0,2]]


def test_exchange_points_and_seeds():
    base = np.ones((10,10), dtype=np.int32)
    donor = np.ones_like(base)
    donor[:,5:] = 2
    rgb = np.full((10,10,3), 100, dtype=np.uint8)
    out, scope = seeded(rgb, base, 1, centers(donor,[1,2]), [])
    assert delta(base,out,scope)["split"] == 1 and (out > 0).all()
    with pytest.raises(ValueError,match="Duplicate"):
        seeded(rgb,base,1,[[1,2],[1,2]],[])
    with pytest.raises(ValueError,match="inside"):
        walker(rgb,base,[1],[[2,2]],[[0,0]],[1,1,5,5])


def test_workbench_preview_undo_close_and_batch(tmp_path):
    from pathology_tools.packets import prepare
    from pathology_tools.composition_bench import Bench
    rgb=np.full((32,32,3),210,dtype=np.uint8)
    rgb[5:13,4:12]=[41,34,72]
    inputs=[tmp_path/key for key in ['A','B']]
    for path in inputs:
        prepare(rgb,[0,0],path.name,path)
    output=tmp_path/'evidence'
    output.mkdir()
    b=Bench(inputs,output,dict(local=True,budget=6,individual=False,batch=4))
    def decision(k,action):
        return dict(field=k,action=action,donor='recover_faint',roi=[],parents=[],children=[],points=[],background=[],reason='visible')
    def submit(rows):
        return b.handle('workbench_submit',dict(ticket=f'R{b.completed+1}',decisions=rows),'id')
    assert len(b.handle('workbench_open',{},'id'))==5
    submit([decision('A','whole'),decision('B','keep')])
    assert b.fields['A'].pending is not None and b.fields['B'].finished
    submit([decision('A','undo')])
    assert b.fields['A'].pending is None
    submit([decision('A','keep')])
    assert b.done and np.array_equal(np.load(output/'A.npz')['labels'],b.fields['A'].bank['baseline'])


def test_random_walker_adds_one_seeded_nucleus():
    rgb=np.full((25,25,3),240,dtype=np.uint8)
    y,x=np.indices(rgb.shape[:2])
    rgb[(x-12)**2+(y-12)**2<=16]=[70,30,90]
    base=np.zeros((25,25),dtype=np.int32)
    base[0:2,:]=9
    out,scope=walker(rgb,base,[],[[12,12]],[[4,4],[20,4],[4,20],[20,20]], [3,3,19,19])
    assert out[12,12]==10 and np.array_equal(out[~scope],base[~scope])
    assert delta(base,out,scope)['added']==1


def test_lung_native_coordinates_and_local_classes(tmp_path):
    from PIL import Image
    from pathology_tools.packets import dump,sha
    from pathology_tools.regional_bench import RegionField,TOOLS
    path=tmp_path/'L'
    path.mkdir()
    pix=np.random.default_rng(18).integers(0,255,(96,120,3),dtype=np.uint8)
    full=tmp_path/'native.png'
    im=Image.fromarray(pix)
    im.save(full)
    im.resize((60,48)).save(path/'source.png')
    for name in TOOLS:
        np.savez_compressed(path/(name+'.npz'),labels=np.ones((48,60),dtype=np.uint8))
    dump(path/'field.json',dict(source_key=sha(full),files={p.name:sha(p) for p in path.iterdir()}))
    f=RegionField(path,full)
    f.apply(dict(action='zoom',roi=[70,0,40,25],reason='right edge'),True,6)
    assert f.render().width>0
    f.apply(dict(action='paint',roi=[],ids=[1],pattern='solid',reason='local architecture'),True,6)
    assert (f.pending[f.slic==1]==3).all() and (f.pending[f.slic!=1]==5).all()
    f.apply(dict(action='undo',reason='review'),True,6)
    assert (f.current==5).all()


def test_merge_then_delete_preserves_adjacent_instance():
    before=np.array([[7,7,9,9,21],[7,7,9,9,21]],dtype='int32')
    one=np.array([[2,2,2,2,0],[2,2,2,2,0]],dtype='int32')
    merged,scope=compose(before,one,[7,9],[2],[0,0,4,2])
    assert delta(before,merged,scope)['merged']==1
    after,scope=compose(merged,one,[22],[],[0,0,4,2])
    assert delta(merged,after,scope)['removed']==1 and (after[:,4]==21).all()
