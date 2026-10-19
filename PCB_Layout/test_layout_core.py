from copy import deepcopy
import unittest
from lxml import etree as ET
from layout_core import (Board, default_recipe, group_refs, load_sizes, size_match,
                         polygon_gap, terminal_paths, replicate, transform_for, transform_point, migrate_view, migrate_replication)


def fixture():
    root = ET.Element('Source',Type='DipTrace-PCB',Units='mm')
    lib = ET.SubElement(root,'Library'); pat = ET.SubElement(lib,'Pattern',PatternStyle='P')
    ET.SubElement(pat,'Name').text = 'RES_0402'
    ET.SubElement(ET.SubElement(pat,'Pads'),'Pad',Id='1',X='0',Y='0')
    shapes = ET.SubElement(pat,'Shapes'); shape = ET.SubElement(shapes,'Shape',Type='Rectangle',Layer='Top Outline')
    ps = ET.SubElement(shape,'Points'); ET.SubElement(ps,'Item',X='-.5',Y='-.25'); ET.SubElement(ps,'Item',X='.5',Y='.25')
    board = ET.SubElement(root,'Board'); layers = ET.SubElement(board,'CopperLayers')
    for i,name in enumerate(['Top','Bottom']):
        ET.SubElement(ET.SubElement(layers,'Lay',Id=str(i)),'Name').text = name
    ET.SubElement(ET.SubElement(board,'ViaStyles'),'ViaStyle',Id='0',Size='.5')
    comps = ET.SubElement(board,'Components'); nets = ET.SubElement(board,'Nets')
    for nid,prefix in [('0','Source'),('1','Destination')]:
        net = ET.SubElement(nets,'Net',Id=nid); ET.SubElement(net,'Name').text = prefix
        ET.SubElement(net,'Pads'); ET.SubElement(net,'Traces')
    def component(cid,ref,x,nid,via=False):
        attrs = dict(Id=str(cid),X=str(x),Y='0',UpdateId='-1')
        attrs.update(dict(Type='Via',ViaStyle='0') if via else dict(PatternStyle='P',Angle='0',Side='Top'))
        c = ET.SubElement(comps,'Component',**attrs); ET.SubElement(c,'RefDes').text = ref
        ET.SubElement(c,'Name').text = 'Static Via' if via else 'RES'
        ET.SubElement(ET.SubElement(c,'Pads'),'Pad',Id='1',NetId=nid)
        ET.SubElement(nets.find(f"Net[@Id='{nid}']/Pads"),'Item',Comp=str(cid),Pad='1')
    for c,ref,x,nid,via in [(0,'R1',0,'0',False),(1,'R2',2,'0',False),(2,'R3',10,'1',False),(3,'R4',12,'1',False),(4,'R9',5,'0',False),(5,'StaticVia1',1,'0',True),(6,'StaticVia2',11,'1',True)]:
        component(c,ref,x,nid,via)
    def trace(nid,tid,a,b,x,y,layer):
        t = ET.SubElement(nets.find(f"Net[@Id='{nid}']/Traces"),'Trace',Id=str(tid),Connected1='Pad',Object1=str(a),SubObject1='1',Point1='0',Connected2='Pad',Object2=str(b),SubObject2='1',Point2='0',PairSeparateTrace='-1')
        p = ET.SubElement(t,'Points')
        for i,pos in enumerate([x,y]):
            ET.SubElement(p,'Point',Id=str(i),X=str(pos),Y='0',Lay=str(layer),Width='.1',ViaStyle='0',PairPoint='-1')
    trace('0',0,0,5,0,1,0); trace('0',1,5,1,1,2,1); trace('0',2,5,4,1,5,0)
    trace('1',0,2,6,10,11,0); trace('1',1,6,3,11,12,1)
    return Board(data=ET.tostring(root))


class LayoutTests(unittest.TestCase):
    def recipe(self,b):
        r = default_recipe(b); r['groups'] = [{'name':'Source','refs':['R1','R2']},{'name':'Target','refs':['R3','R4']}]
        r['view'].update(tracks=True,vias=True); r['replication'].update(dx=4,count=3)
        return r

    def test_size_priority_and_fallback(self):
        b = fixture(); p = b.parts['R1']
        entries = {'sizes':[{'pattern':'RES_0402','width_mm':1,'height_mm':1}, {'name':'RES','width_mm':2,'height_mm':2}, {'pattern':'RES_0402','name':'RES','width_mm':3,'height_mm':3}]}
        self.assertEqual(size_match(p,entries)[1]['width_mm'],3)
        entries['sizes'].pop(); self.assertEqual(size_match(p,entries)[1]['width_mm'],2)
        entries['sizes'].pop(); self.assertEqual(size_match(p,entries)[1]['width_mm'],1)
        paths,poly,origin,index = b.geometry(p,{'sizes':[]})
        self.assertEqual(origin,'Component outline'); self.assertEqual(len(poly),4)
        p.footprint.find('./Shapes/Shape').set('Layer','Top Silk')
        self.assertEqual(b.geometry(p,{'sizes':[]})[2],'Silkscreen')

    def test_internal_via_chain_and_external_branch(self):
        b = fixture(); r = b.routing(['R1','R2'],True,True,{'0','1'})
        self.assertEqual(set(r.segments),{('0','0'),('0','1')}); self.assertEqual(r.vias,{'5'})
        self.assertFalse(b.routing(['R1','R2'],True,False,{'0','1'}).segments)
        self.assertFalse(b.routing(['R1','R2'],True,True,{'0'}).segments)

    def test_dangling_cycle_excluded_but_parallel_internal_paths_kept(self):
        edges = [('a','x'),('x','b'),('x','y'),('y','z'),('z','x'),('a','c'),('c','b')]
        graph = {n:set() for pair in edges for n in pair}
        for a,b in edges:
            graph[a].add(b); graph[b].add(a)
        chosen = terminal_paths(graph,{'a','b'})
        self.assertEqual(chosen,{frozenset(p) for p in [('a','x'),('x','b'),('a','c'),('c','b')]})

    def test_create_ids_nets_and_repeat(self):
        b = fixture(); recipe = self.recipe(b); original = ET.tostring(b.root)
        copied,groups = replicate(b,recipe)
        self.assertEqual(ET.tostring(b.root),original)
        self.assertEqual(len(copied.parts),len(b.parts)+6)
        self.assertEqual(len(copied.traces),len(b.traces)+4)
        self.assertEqual(len(copied.nets),len(b.nets)+2)
        self.assertEqual(len(set(p.ref for p in copied.parts.values())),len(copied.parts))
        for group in groups[1:]:
            self.assertEqual([copied.parts[r].x for r in group['refs']],[4*(groups.index(group)),2+4*(groups.index(group))])
        recipe['groups'] = groups; recipe['replication']['mode'] = 'Arrange existing groups'
        repeated,_ = replicate(copied,recipe)
        self.assertEqual(len(repeated.parts),len(copied.parts)); self.assertEqual(len(repeated.traces),len(copied.traces))
        self.assertEqual([p.x for p in repeated.parts.values() if not p.via],[p.x for p in copied.parts.values() if not p.via])

    def test_copies_preserve_the_prefix_before_the_final_counter(self):
        b = fixture()
        for old, new in [('R1', 'J6-1'), ('R2', 'J6-2'), ('R3', 'J6-10')]:
            b.parts[old].element.find('RefDes').text = new
        b = Board(data=ET.tostring(b.root))
        recipe = default_recipe(b)
        recipe['groups'] = [{'name': 'Source', 'refs': ['J6-1', 'J6-2']}]
        recipe['replication'].update(mode='Create copies', count=2, tracks=False, vias=False)
        original = ET.tostring(b.root)
        copied, groups = replicate(b, recipe)
        self.assertEqual(groups[1]['refs'], ['J6-3', 'J6-4'])
        self.assertIn('J6-10', copied.parts)
        self.assertEqual(ET.tostring(b.root), original)

    def test_existing_preserves_net_ids_and_external_copper(self):
        b = fixture(); recipe = self.recipe(b); recipe['replication'].update(mode='Arrange existing groups',dx=4)
        original_external = ET.tostring(b.traces['0','2'])
        out,groups = replicate(b,recipe)
        self.assertEqual(out.parts['R3'].x,4); self.assertEqual(out.parts['R4'].x,6)
        self.assertEqual(set(out.nets),set(b.nets)); self.assertEqual(ET.tostring(out.traces['0','2']),original_external)
        self.assertEqual(out.parts['R3'].element.find('./Pads/Pad').get('NetId'),'1')

    def test_partial_trace_clipped_at_junction(self):
        b = fixture()
        net = b.nets['0']; traces = net.find('Traces')
        for child in list(traces):traces.remove(child)
        # R1 -> junction -> external R9; R2 joins at the junction.
        t = ET.SubElement(traces,'Trace',Id='0',Connected1='Pad',Object1='0',SubObject1='1',Point1='0',Connected2='Pad',Object2='4',SubObject2='1',Point2='0')
        points = ET.SubElement(t,'Points')
        for i,x in enumerate([0,1,5]):ET.SubElement(points,'Point',Id=str(i),X=str(x),Y='0',Lay='0',Width='.1')
        t = ET.SubElement(traces,'Trace',Id='1',Connected1='Pad',Object1='1',SubObject1='1',Point1='0',Connected2='Trace',Object2='0',SubObject2='0',Point2='1')
        points = ET.SubElement(t,'Points')
        for i,x in enumerate([2,1]):ET.SubElement(points,'Point',Id=str(i),X=str(x),Y='0',Lay='0',Width='.1')
        b = Board(data=ET.tostring(b.root)); route = b.routing(['R1','R2'],True,True,{'0','1'})
        self.assertEqual(route.segments['0','0'],{0})
        recipe = self.recipe(b); out,groups = replicate(b,recipe)
        first_copy_net = out.parts[groups[1]['refs'][0]].element.find('./Pads/Pad').get('NetId')
        for trace in out.nets[first_copy_net].findall('./Traces/Trace'):
            self.assertNotIn('9',trace.attrib.values())
            self.assertLessEqual(max(float(p.get('X')) for p in trace.findall('./Points/Point')),6)

    def test_rotational_grid_and_clearance(self):
        b = fixture(); r = self.recipe(b)['replication']; r.update(layout='Grid',columns=2,rows=2,column_dx=3,column_dy=1,row_dx=-1,row_dy=4)
        self.assertEqual(transform_point((0,0),transform_for(r,3,4)),(2,5))
        r.update(layout='Rotational',automatic_angle=True,center_x=0,center_y=0)
        x,y = transform_point((2,0),transform_for(r,1,4)); self.assertAlmostEqual(x,0); self.assertAlmostEqual(y,2)
        a = [(0,0),(1,0),(1,1),(0,1)]; c = [(1.2,0),(2.2,0),(2.2,1),(1.2,1)]
        self.assertAlmostEqual(polygon_gap(a,c),.2); self.assertEqual(polygon_gap(a,a),0)

    def test_embedded_vias_and_vias_only_copy(self):
        b = fixture(); container = b.nets['0'].find('Traces')
        for t in list(container):container.remove(t)
        t = ET.SubElement(container,'Trace',Id='0',Connected1='Pad',Object1='0',SubObject1='1',Point1='0',Connected2='Pad',Object2='1',SubObject2='1',Point2='0')
        points = ET.SubElement(t,'Points')
        for i,(x,layer) in enumerate([(0,0),(1,0),(1,1),(2,1)]):
            ET.SubElement(points,'Point',Id=str(i),X=str(x),Y='0',Lay=str(layer),Width='.1',ViaStyle='0')
        b = Board(data=ET.tostring(b.root)); route = b.routing(['R1','R2'],True,True,{'0','1'})
        self.assertEqual(route.embedded,{('0','0',1)})
        self.assertFalse(b.routing(['R1','R2'],True,False,{'0','1'}).segments)
        recipe = self.recipe(b); recipe['replication'].update(count=2,tracks=False)
        out,groups = replicate(b,recipe)
        self.assertEqual(len(out.traces),len(b.traces)); self.assertEqual(len(out.replication_routing[1].vias),1)
        via = out.by_id[next(iter(out.replication_routing[1].vias))]
        self.assertEqual(via.x,5)

    def test_existing_source_anchor_preserves_group_order(self):
        b = fixture(); r = self.recipe(b); r['replication'].update(mode='Arrange existing groups',source=1,dx=4)
        out,groups = replicate(b,r)
        self.assertEqual([g['name'] for g in groups],['Source','Target'])
        self.assertEqual(out.parts['R3'].x,10); self.assertEqual(out.parts['R1'].x,6)

    def test_rounded_pads_and_layer_membership(self):
        b = fixture(); library = b.root.find('Library')
        style = ET.SubElement(library,'PadStyle',Name='S',Type='Surface',Side='Top')
        ET.SubElement(style,'MainStack',Shape='Rectangle',Width='2',Height='4',Corner='25')
        b.parts['R1'].footprint.find('./Pads/Pad').set('Style','S')
        b = Board(data=ET.tostring(b.root)); part = b.parts['R1']
        points = b.pad_polygons(part)[0][1]
        self.assertEqual(len(points),36)
        self.assertAlmostEqual(points[0][0],-1); self.assertAlmostEqual(points[0][1],-1.5)
        self.assertFalse(any(abs(x+1)<1e-8 and abs(y+2)<1e-8 for x,y in points))
        self.assertEqual(b.pad_layers(part,'1'),{'0'})
        part.side = 'Bottom'; self.assertEqual(b.pad_layers(part,'1'),{'1'})
        b.pad_styles['S'].set('Type','Through'); self.assertEqual(b.pad_layers(part,'1'),{'0','1'})

    def test_body_origin_is_local_and_leaves_pads_unchanged(self):
        import math
        b = fixture(); part = b.parts['R1']; part.angle = math.pi/2; part.flip = True
        ET.SubElement(part.footprint,'Origin',X='.3',Y='.4')
        before = b.pads(part)
        data = {'sizes':[{'pattern':part.pattern,'width_mm':2,'height_mm':1,'rotation_deg':30,'origin_x_mm':.2,'origin_y_mm':-.1}]}
        _,poly,_,_ = b.geometry(part,data)
        self.assertAlmostEqual(sum(x for x,y in poly)/4,-.3)
        self.assertAlmostEqual(sum(y for x,y in poly)/4,-.5)
        self.assertEqual(b.pads(part),before)
        del data['sizes'][0]['origin_x_mm']; del data['sizes'][0]['origin_y_mm']
        _,poly,_,_ = b.geometry(part,data)
        self.assertAlmostEqual(sum(x for x,y in poly)/4,-.4)
        self.assertAlmostEqual(sum(y for x,y in poly)/4,-.3)

    def test_legacy_orientation_migrates_without_changing_view(self):
        for down in [False,True]:
            for rotated in [False,True]:
                view = {'y_down':down,'rotate_workspace':rotated}; migrate_view(view)
                legacy = -1 if view.get('legacy_rotation_deg') == 180 else 1
                actual = ((-1 if view['mirror'] else 1)*legacy,-legacy)
                sign = -1 if rotated else 1
                self.assertEqual(actual,(sign,sign*(1 if down else -1)))

    def test_replication_selection_is_independent_of_view(self):
        b = fixture(); recipe = self.recipe(b)
        recipe['view'].update(tracks=False,vias=False)
        out,groups = replicate(b,recipe)
        self.assertEqual(len(out.traces),len(b.traces)+4)
        recipe['replication'].update(tracks=False,vias=False)
        recipe['view'].update(tracks=True,vias=True)
        out,groups = replicate(b,recipe)
        self.assertEqual(len(out.traces),len(b.traces))
        self.assertEqual(len(out.parts),len(b.parts)+4)

    def test_migration_preserves_old_copy_choices_once(self):
        b = fixture(); recipe = self.recipe(b)
        for key in ('tracks','vias','layers'): del recipe['replication'][key]
        recipe['view'].update(tracks=False,vias=True,layers=['1'])
        migrate_replication(recipe,b)
        self.assertFalse(recipe['replication']['tracks']); self.assertTrue(recipe['replication']['vias'])
        self.assertEqual(recipe['replication']['layers'],['1']); self.assertNotIn('layers',recipe['view'])
        recipe['view'].update(tracks=True,vias=False)
        migrate_replication(recipe,b)
        self.assertFalse(recipe['replication']['tracks']); self.assertTrue(recipe['replication']['vias'])


if __name__ == '__main__':
    unittest.main()
