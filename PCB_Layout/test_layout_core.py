from copy import deepcopy
import math
import unittest
from lxml import etree as ET
from layout_core import (Board, default_recipe, group_refs, load_sizes, size_match,
                         polygon_gap, terminal_paths, replicate, transform_for, transform_point, migrate_view, migrate_replication, grid_cell, interlink_group_map, repair_ratlines, repair_net_membership, validate_board)


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


def linked_grid(via=False):
    """Six two-pad parts, wired as a chain with a custom link at the row end."""
    b = fixture(); pattern = b.root.find('./Library/Pattern')
    pattern.find('./Pads/Pad').set('X','-.2')
    ET.SubElement(pattern.find('Pads'),'Pad',Id='2',X='.2',Y='0')
    components = b.board.find('Components'); nets = b.board.find('Nets')
    for container in (components,nets):
        for child in list(container):
            container.remove(child)
    for i in range(6):
        x,y,angle = (i,0,0) if i < 3 else (5-i,3,math.pi)
        c = ET.SubElement(components,'Component',Id=str(i),X=str(x),Y=str(y),Angle=str(angle),PatternStyle='P',Side='Top')
        ET.SubElement(c,'RefDes').text = f'U{i+1}'; ET.SubElement(c,'Name').text = 'Link'
        pads = ET.SubElement(c,'Pads')
        ET.SubElement(pads,'Pad',Id='1',NetId=str(i-1) if i else '-1')
        ET.SubElement(pads,'Pad',Id='2',NetId=str(i) if i < 5 else '-1')
    for i in range(5):
        net = ET.SubElement(nets,'Net',Id=str(i)); ET.SubElement(net,'Name').text = f'Net {i}'
        pads = ET.SubElement(net,'Pads')
        ET.SubElement(pads,'Item',Comp=str(i),Pad='2'); ET.SubElement(pads,'Item',Comp=str(i+1),Pad='1')
        ET.SubElement(net,'Traces')
    b = Board(data=ET.tostring(b.root))
    for i in range(5):
        a = b.pads(b.parts[f'U{i+1}'])['2']; z = b.pads(b.parts[f'U{i+2}'])['1']
        points = [a,(.4,-.3),(.6,-.3),z] if i == 0 else [a,z]
        trace = ET.SubElement(b.nets[str(i)].find('Traces'),'Trace',Id='0',Connected1='Pad',Object1=str(i),SubObject1='2',Point1='-1',Connected2='Pad',Object2=str(i+1),SubObject2='1',Point2='0',PairSeparateTrace='-1')
        container = ET.SubElement(trace,'Points')
        for j,(x,y) in enumerate(points):
            ET.SubElement(container,'Point',Id=str(j),X=str(x),Y=str(y),Lay='0',Width='.1',PairPoint='-1')
    if via:
        c = ET.SubElement(b.board.find('Components'),'Component',Id='6',Type='Via',ViaStyle='0',X='.4',Y='-.3')
        ET.SubElement(c,'RefDes').text = 'StaticVia1'; ET.SubElement(c,'Name').text = 'Static Via'
        ET.SubElement(ET.SubElement(c,'Pads'),'Pad',Id='1',NetId='0')
        ET.SubElement(b.nets['0'].find('Pads'),'Item',Comp='6',Pad='1')
        trace = b.nets['0'].find('./Traces/Trace'); points = trace.find('Points')
        trace.set('Connected2','Pad'); trace.set('Object2','6'); trace.set('SubObject2','1')
        for point in list(points)[2:]:
            points.remove(point)
        tail = ET.SubElement(b.nets['0'].find('Traces'),'Trace',Id='1',Connected1='Pad',Object1='6',SubObject1='1',Point1='-1',Connected2='Pad',Object2='1',SubObject2='1',Point2='0',PairSeparateTrace='-1')
        container = ET.SubElement(tail,'Points')
        for j,(x,y) in enumerate([(.4,-.3),(.6,-.3),(.8,0)]):
            ET.SubElement(container,'Point',Id=str(j),X=str(x),Y=str(y),Lay='1',Width='.1',PairPoint='-1')
    recipe = default_recipe(b)
    recipe['groups'] = [{'name':f'Group {i+1}','refs':[f'U{i+1}']} for i in range(6)]
    recipe['replication'].update(mode='Arrange existing groups',layout='Grid',columns=3,rows=2,
        column_dx=1,column_dy=0,row_dx=0,row_dy=3,rotate_odd=True,rotate_odd_axis='Rows')
    return Board(data=ET.tostring(b.root)),recipe


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

    def test_arrange_does_not_require_external_pins_to_have_matching_nets(self):
        b = fixture()
        ET.SubElement(b.parts['R1'].footprint.find('Pads'),'Pad',Id='2',X='1',Y='0')
        for part in b.parts.values():
            if not part.via:
                ET.SubElement(part.element.find('Pads'),'Pad',Id='2',NetId='-1')
        external = ET.SubElement(b.board.find('Nets'),'Net',Id='2')
        ET.SubElement(external,'Name').text = 'External signal'
        pads = ET.SubElement(external,'Pads'); ET.SubElement(external,'Traces')
        b.parts['R1'].element.find("./Pads/Pad[@Id='2']").set('NetId','2')
        ET.SubElement(pads,'Item',Comp=b.parts['R1'].id,Pad='2')
        b = Board(data=ET.tostring(b.root))
        r = self.recipe(b); r['replication']['mode'] = 'Arrange existing groups'
        before = ET.tostring(b.root)
        out,_ = replicate(b,r)
        self.assertEqual(out.parts['R3'].element.find("./Pads/Pad[@Id='2']").get('NetId'),'-1')
        self.assertEqual(ET.tostring(out.nets['2']),ET.tostring(b.nets['2']))
        self.assertEqual(ET.tostring(b.root),before)

    def test_net_mapping_is_only_required_when_copper_is_copied(self):
        b = fixture()
        for trace in list(b.nets['1'].find('Traces')):
            trace.getparent().remove(trace)
        pad_item = b.nets['1'].find("./Pads/Item[@Comp='3']")
        pad_item.getparent().remove(pad_item)
        b.parts['R4'].element.find('./Pads/Pad').set('NetId','2')
        separate = ET.SubElement(b.board.find('Nets'),'Net',Id='2')
        ET.SubElement(separate,'Name').text = 'Separate target net'
        ET.SubElement(ET.SubElement(separate,'Pads'),'Item',Comp='3',Pad='1')
        ET.SubElement(separate,'Traces')
        b = Board(data=ET.tostring(b.root))
        r = self.recipe(b); r['replication'].update(mode='Arrange existing groups',tracks=True,vias=True)
        with self.assertRaisesRegex(ValueError,'net mapping for internal copper'):
            replicate(b,r)
        r['replication'].update(tracks=False,vias=False)
        out,_ = replicate(b,r)
        self.assertEqual(out.parts['R3'].x,4)
        self.assertEqual(out.parts['R4'].element.find('./Pads/Pad').get('NetId'),'2')

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

    def test_grid_rotates_whole_alternate_rows_or_columns(self):
        b = fixture(); r = self.recipe(b)
        r['groups'] = r['groups'][:1]
        s = r['replication']; s.update(layout='Grid',columns=3,rows=2,column_dx=4,column_dy=1,row_dx=-2,row_dy=6,rotate_odd=True)
        out,groups = replicate(b,r)
        center = (1,0)
        for i,g in enumerate(groups):
            row,col = divmod(i,3)
            if row % 2: col = 2-col
            x,y = center[0]+col*4+row*-2,center[1]+col+row*6
            a,z = [out.parts[ref] for ref in g['refs']]
            self.assertAlmostEqual((a.x+z.x)/2,x); self.assertAlmostEqual((a.y+z.y)/2,y)
            self.assertAlmostEqual(a.angle,math.pi if row % 2 else 0)
            if row % 2: self.assertGreater(a.x,z.x)
        # Repeating Arrange with a source in a turned row keeps that source anchored.
        r['groups'] = groups; s.update(mode='Arrange existing groups',source=4)
        again,_ = replicate(out,r)
        for ref,p in out.parts.items():
            if not p.via:
                self.assertAlmostEqual(again.parts[ref].x,p.x); self.assertAlmostEqual(again.parts[ref].y,p.y)
                self.assertAlmostEqual(again.parts[ref].angle,p.angle)
        s.update(mode='Create copies',source=0,rotate_odd_axis='Columns')
        r['groups'] = r['groups'][:1]
        out,groups = replicate(b,r)
        a = out.parts[groups[1]['refs'][0]]
        self.assertAlmostEqual(a.x,4); self.assertAlmostEqual(a.y,7)
        self.assertAlmostEqual(a.angle,math.pi)

    def test_interlink_offsets_stop_at_each_grid_boundary(self):
        _,r = linked_grid(); s = r['replication']
        self.assertEqual(interlink_group_map(s,{0,1},0,3,6),{0:3,1:4})
        self.assertIsNone(interlink_group_map(s,{0,1},0,2,6))
        self.assertEqual(interlink_group_map(s,{0,3},0,2,6),{0:2,3:5})
        self.assertIsNone(interlink_group_map(s,{0,3},0,4,6))
        self.assertIsNone(interlink_group_map(s,{0,4},0,2,6))
        s.update(layout='Linear')
        self.assertIsNone(interlink_group_map(s,{0,1},0,5,6))
        s.update(layout='Rotational',automatic_angle=True)
        self.assertEqual(interlink_group_map(s,{0,1},0,5,6),{0:5,1:0})

    def test_column_first_grid_order_rotation_and_boundaries(self):
        b = fixture(); r = self.recipe(b); r['groups'] = r['groups'][:1]
        s = r['replication']; s.update(layout='Grid',grid_order='Column first',columns=3,rows=2,
            column_dx=4,column_dy=0,row_dx=0,row_dy=-3,rotate_odd=True,rotate_odd_axis='Columns')
        out,groups = replicate(b,r)
        self.assertEqual([grid_cell(s,i) for i in range(6)],[(0,0),(0,1),(1,0),(1,1),(2,0),(2,1)])
        for i,g in enumerate(groups):
            column,row = divmod(i,2)
            if column % 2:row = 1-row
            a,z = [out.parts[ref] for ref in g['refs']]
            self.assertAlmostEqual((a.x+z.x)/2,1+column*4)
            self.assertAlmostEqual((a.y+z.y)/2,row*-3)
            self.assertAlmostEqual(a.angle,math.pi if column % 2 else 0)
        # Consecutive group numbers now form vertical links, with no column wrap.
        self.assertEqual(interlink_group_map(s,{0,1},0,2,6),{0:2,1:3})
        self.assertIsNone(interlink_group_map(s,{0,1},0,1,6))
        # A horizontal offset advances by the row count, stopping at the last column.
        self.assertEqual(interlink_group_map(s,{0,2},0,3,6),{0:3,2:5})
        self.assertIsNone(interlink_group_map(s,{0,2},0,4,6))
        r['groups'] = groups; s.update(mode='Arrange existing groups',source=3)
        again,_ = replicate(out,r)
        for ref,p in out.parts.items():
            if not p.via:
                self.assertAlmostEqual(again.parts[ref].x,p.x); self.assertAlmostEqual(again.parts[ref].y,p.y)

    def test_alternate_row_rotation_aligns_footprint_origins(self):
        b = fixture(); ET.SubElement(b.parts['R1'].footprint,'Origin',X='.0125',Y='-.0125')
        b.parts['R1'].element.set('Angle',str(1.5*math.pi))
        b = Board(data=ET.tostring(b.root)); r = default_recipe(b)
        r['groups'] = [{'name':'Source','refs':['R1']}]
        r['replication'].update(layout='Grid',columns=3,rows=2,column_dx=1.27,column_dy=0,row_dx=0,row_dy=1.27,rotate_odd=True,rotate_odd_axis='Rows',tracks=False,vias=False)
        out,groups = replicate(b,r)
        first,last = [out.parts[groups[i]['refs'][0]] for i in [0,5]]
        a = (first.x,first.y); z = (last.x,last.y)
        self.assertAlmostEqual(a[0],z[0]); self.assertAlmostEqual(z[1]-a[1],1.27)
        self.assertAlmostEqual(abs(float(first.element.get('X'))-float(last.element.get('X'))),.025)

    def test_xml_origin_is_normalized_once_for_rotations_mirroring_and_units(self):
        from layout_core import rotate
        for units,factor in [('mm',1),('mil',.0254),('inch',25.4)]:
            for angle in (0,math.pi/2,math.pi,1.5*math.pi):
                for flipped in (False,True):
                    with self.subTest(units=units,angle=angle,flipped=flipped):
                        b = fixture(); b.root.set('Units',units)
                        p = b.parts['R1']; p.element.set('X','10'); p.element.set('Y','20'); p.element.set('Angle',str(angle))
                        if flipped:p.element.set('Flip','Y')
                        ET.SubElement(p.footprint,'Origin',X='.3',Y='.4')
                        data = ET.tostring(b.root); b = Board(data=data); p = b.parts['R1']
                        ox,oy = rotate(-.3 if flipped else .3,.4,angle)
                        self.assertAlmostEqual(p.x,(10+ox)*factor); self.assertAlmostEqual(p.y,(20+oy)*factor)
                        self.assertEqual(b.local_to_board(p,(0,0)),(p.x,p.y))
                        self.assertAlmostEqual(b.pads(p)['1'][0],10*factor); self.assertAlmostEqual(b.pads(p)['1'][1],20*factor)
                        _,polygon,_,_ = b.geometry(p,{'sizes':[{'pattern':p.pattern,'width_mm':1,'height_mm':1}]})
                        self.assertAlmostEqual(sum(x for x,y in polygon)/4,p.x); self.assertAlmostEqual(sum(y for x,y in polygon)/4,p.y)
                        self.assertEqual(ET.tostring(b.root),data)

    def test_net_membership_repaired_from_pad_net_ids(self):
        b = fixture(); original = [dict(p.attrib) for part in b.parts.values() for p in part.element.findall('./Pads/Pad')]
        pads = b.nets['0'].find('Pads'); pads.remove(pads[0])
        ET.SubElement(pads,'Item',Comp='999',Pad='1')
        repair_net_membership(b);validate_board(b)
        self.assertFalse(b.nets['0'].findall("./Pads/Item[@Comp='999']"))
        self.assertTrue(b.nets['0'].findall("./Pads/Item[@Comp='0']"))
        self.assertEqual([dict(p.attrib) for part in b.parts.values() for p in part.element.findall('./Pads/Pad')],original)

    def test_interlinks_repeat_with_turned_rows_but_keep_custom_edge_routing(self):
        for via in (False,True):
            with self.subTest(via=via):
                b,r = linked_grid(via); original = ET.tostring(b.root)
                edge = ET.tostring(b.nets['2'])
                out,groups = replicate(b,r)
                self.assertEqual(out.interlink_summary,(3,2))
                self.assertEqual(ET.tostring(out.nets['2']),edge)
                self.assertEqual(ET.tostring(b.root),original)
                for nid in ('0','1','3','4'):
                    route = out.routing([f'U{int(nid)+1}',f'U{int(nid)+2}'],True,True,set(out.layers))
                    self.assertEqual(sum(len(edges) for edges in route.segments.values()),3)
                    self.assertEqual(len(route.vias),int(via))
                    for key in route.segments:
                        trace = out.traces[key]; points = trace.findall('./Points/Point')
                        for side,point in [('1',points[0]),('2',points[-1])]:
                            if trace.get('Connected'+side) == 'Pad':
                                position = out.pads(out.by_id[trace.get('Object'+side)])[trace.get('SubObject'+side)]
                                self.assertLess(math.dist(position,(float(point.get('X')),float(point.get('Y')))),1e-6)
                again,_ = replicate(out,r)
                self.assertEqual(len(again.parts),len(out.parts)); self.assertEqual(len(again.traces),len(out.traces))
                self.assertEqual(ET.tostring(again.nets['2']),edge)

    def test_interlink_copy_creates_missing_trace_container_and_can_remove_skipped_edges(self):
        b,r = linked_grid()
        for nid in ('1','3','4'):
            traces = b.nets[nid].find('Traces'); traces.getparent().remove(traces)
        b = Board(data=ET.tostring(b.root))
        r['replication']['preserve_skipped_routing'] = False
        out,_ = replicate(b,r)
        self.assertFalse(out.nets['2'].findall('./Traces/Trace'))
        for nid in ('0','1','3','4'):
            self.assertEqual(len(out.nets[nid].findall('./Traces/Trace')),1)
            self.assertEqual(len(out.nets[nid].find('./Traces/Trace/Points')),4)
        self.assertEqual(set(out.nets),set(b.nets))

    def test_preview_includes_preserved_edge_tracks_and_vias_only_when_kept(self):
        for preserve in (False,True):
            with self.subTest(preserve=preserve):
                b,r = linked_grid(True)
                # Add a private via to a custom column-break connection.
                via = deepcopy(b.by_id['6'].element); via.set('Id','7')
                via.set('X','1.5'); via.set('Y','3'); via.find('RefDes').text = 'StaticVia2'
                via.find('./Pads/Pad').set('NetId','3'); b.board.find('Components').append(via)
                ET.SubElement(b.nets['3'].find('Pads'),'Item',Comp='7',Pad='1')
                trace = b.nets['3'].find('./Traces/Trace'); tail = deepcopy(trace); tail.set('Id','1')
                trace.set('Object2','7'); trace.set('SubObject2','1')
                trace.find('./Points/Point[@Id="1"]').set('X','1.5')
                tail.set('Object1','7'); tail.set('SubObject1','1')
                tail.find('./Points/Point[@Id="0"]').set('X','1.5')
                b.nets['3'].find('Traces').append(tail); b = Board(data=ET.tostring(b.root))
                r['replication'].update(grid_order='Column first',rotate_odd_axis='Columns',
                    row_dy=-3,preserve_skipped_routing=preserve)
                original = {nid:ET.tostring(b.nets[nid]) for nid in ['1','3']}
                out,_ = replicate(b,r)
                shown = {key for route in out.replication_routing for key in route.segments}
                shown_vias = set().union(*(route.vias for route in out.replication_routing))
                self.assertEqual('7' in shown_vias,preserve)
                self.assertEqual('7' in out.by_id,preserve)
                for nid,data in original.items():
                    self.assertEqual((nid,'0') in shown,preserve)
                    if preserve:
                        self.assertEqual(ET.tostring(out.nets[nid]),data)
                    else:
                        self.assertFalse(out.nets[nid].findall('./Traces/Trace'))
                # Row-first keeps a custom edge with an embedded via.
                b,r = linked_grid()
                trace = b.nets['2'].find('./Traces/Trace'); points = trace.find('Points')
                a,z = list(points); points.remove(z)
                ET.SubElement(points,'Point',Id='1',X='2.5',Y='1.5',Lay='0',Width='.1',ViaStyle='0',PairPoint='-1')
                z.set('Id','2'); z.set('Lay','1'); points.append(z)
                b = Board(data=ET.tostring(b.root)); r['replication']['preserve_skipped_routing'] = preserve
                out,_ = replicate(b,r)
                shown_embedded = set().union(*(route.embedded for route in out.replication_routing))
                self.assertEqual(('2','0',1) in shown_embedded,preserve)

    def test_interlinks_use_electrical_references_when_grid_pitch_changes(self):
        for via in (False,True):
            with self.subTest(via=via):
                b,r = linked_grid(via); r['replication'].update(column_dx=.8,row_dy=2.5)
                out,_ = replicate(b,r)
                for nid in ('0','1','3','4'):
                    route = out.routing([f'U{int(nid)+1}',f'U{int(nid)+2}'],True,True,set(out.layers))
                    self.assertEqual(sum(len(edges) for edges in route.segments.values()),3)
                    for key in route.segments:
                        t = out.traces[key]; points = t.findall('./Points/Point')
                        for side,p in [('1',points[0]),('2',points[-1])]:
                            if t.get('Connected'+side) == 'Pad':
                                expected = out.pads(out.by_id[t.get('Object'+side)])[t.get('SubObject'+side)]
                                self.assertLess(math.dist(expected,(float(p.get('X')),float(p.get('Y')))),1e-7)

    def test_column_first_interlinks_repeat_inside_columns_and_preserve_column_breaks(self):
        b,r = linked_grid(True)
        r['replication'].update(grid_order='Column first',rotate_odd_axis='Columns',row_dy=-3)
        original_breaks = {nid:ET.tostring(b.nets[nid]) for nid in ['1','3']}
        out,_ = replicate(b,r)
        for nid in ['0','2','4']:
            route = out.routing([f'U{int(nid)+1}',f'U{int(nid)+2}'],True,True,set(out.layers))
            self.assertEqual(sum(len(edges) for edges in route.segments.values()),3)
            self.assertEqual(len(route.vias),1)
        for nid,old in original_breaks.items():
            self.assertEqual(ET.tostring(out.nets[nid]),old)

    def test_ratlines_repair_deleted_vias_and_keep_unrouted_net_connections(self):
        b = fixture(); t = b.traces['0','1']; t.getparent().remove(t)
        container = ET.SubElement(b.board,'Ratlines')
        ET.SubElement(container,'Ratline',Id='76',Comp1='1',Pad1='1',Comp2='999',Pad2='1',X1='2',Y1='0',X2='1',Y2='0',Hidden='N')
        b.reindex()
        with self.assertRaisesRegex(ValueError,'Ratline'):
            validate_board(b)
        repair_ratlines(b); validate_board(b)
        items = b.board.findall('./Ratlines/Ratline')
        self.assertEqual(len(items),1)
        self.assertEqual({items[0].get('Comp1'),items[0].get('Comp2')},{'1','5'})
        self.assertEqual(items[0].get('Id'),'0')
        first = ET.tostring(b.board.find('Ratlines'))
        repair_ratlines(b)
        self.assertEqual(ET.tostring(b.board.find('Ratlines')),first)

    def test_circular_interlinks_wrap_without_copying_incoming_and_outgoing_twice(self):
        b,r = linked_grid(); r['replication'].update(layout='Rotational',automatic_angle=True,center_x=0,center_y=0)
        for i in range(6):
            part = b.parts[f'U{i+1}']; angle = i*math.tau/6
            part.element.set('X',str(10*math.cos(angle))); part.element.set('Y',str(10*math.sin(angle))); part.element.set('Angle',str(angle))
        b.parts['U1'].element.find("./Pads/Pad[@Id='1']").set('NetId','5')
        b.parts['U6'].element.find("./Pads/Pad[@Id='2']").set('NetId','5')
        net = ET.SubElement(b.board.find('Nets'),'Net',Id='5'); ET.SubElement(net,'Name').text = 'Net 5'
        pads = ET.SubElement(net,'Pads'); ET.SubElement(pads,'Item',Comp='5',Pad='2'); ET.SubElement(pads,'Item',Comp='0',Pad='1')
        ET.SubElement(net,'Traces')
        b = Board(data=ET.tostring(b.root))
        for i in range(6):
            container = b.nets[str(i)].find('Traces')
            for child in list(container): container.remove(child)
            trace = ET.SubElement(container,'Trace',Id='0',Connected1='Pad',Object1=str(i),SubObject1='2',Point1='-1',Connected2='Pad',Object2=str((i+1)%6),SubObject2='1',Point2='0')
            points = ET.SubElement(trace,'Points')
            a = b.pads(b.parts[f'U{i+1}'])['2']; z = b.pads(b.parts[f'U{(i+1)%6+1}'])['1']
            path = [a,(8,3),z] if i == 0 else [a,z]
            for j,(x,y) in enumerate(path): ET.SubElement(points,'Point',Id=str(j),X=str(x),Y=str(y),Lay='0',Width='.1')
        b = Board(data=ET.tostring(b.root))
        out,_ = replicate(b,r)
        self.assertEqual(out.interlink_summary,(5,0))
        self.assertEqual(len(out.traces),6)
        self.assertTrue(all(len(t.find('Points')) == 3 for t in out.traces.values()))
        again,_ = replicate(out,r)
        self.assertEqual(len(again.traces),6)

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
        b = fixture(); part = b.parts['R1']; part.element.set('Angle',str(math.pi/2)); part.element.set('Flip','Y')
        ET.SubElement(part.footprint,'Origin',X='.3',Y='.4')
        b = Board(data=ET.tostring(b.root)); part = b.parts['R1']
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
