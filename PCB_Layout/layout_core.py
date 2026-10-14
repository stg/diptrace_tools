"""PCB geometry and internal routing, shared by the viewer and replication."""
from __future__ import annotations

from collections import defaultdict, deque
from copy import deepcopy
from dataclasses import dataclass, field
import json
import hashlib
import math
from pathlib import Path
import re

from lxml import etree as ET

APP_DIR = Path(__file__).resolve().parent
SIZES_PATH = APP_DIR / 'component_sizes.json'
SETTINGS_PATH = APP_DIR / 'settings.json'
DEFAULTS_PATH = APP_DIR / 'project_defaults.json'


def write_json(path, data):
    path = Path(path)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temp.replace(path)


def load_sizes(path=SIZES_PATH):
    data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if data.get('schema_version') != 1 or not isinstance(data.get('sizes'), list):
        raise ValueError('Component sizes must contain schema_version 1 and a sizes list.')
    for entry in data['sizes']:
        if not isinstance(entry,dict) or any(key in entry and not isinstance(entry[key],str) for key in ('pattern','name')):
            raise ValueError('Size entries must be objects with text pattern/name matches.')
        if not any(isinstance(entry.get(k), str) and entry[k].strip() for k in ('name', 'pattern')):
            raise ValueError('Each size needs a name, a pattern, or both.')
        for key in ('width_mm', 'height_mm'):
            if not math.isfinite(float(entry[key])) or float(entry[key]) <= 0:
                raise ValueError(f'{key} must be a positive finite number.')
        if not math.isfinite(float(entry.get('rotation_deg', 0))):
            raise ValueError('rotation_deg must be finite.')
        if entry.get('pattern_match', 'exact') not in ('exact', 'contains'):
            raise ValueError('pattern_match must be exact or contains.')
    return data


def size_match(part, data):
    matches = []
    for index, entry in enumerate(data['sizes']):
        pattern, name = entry.get('pattern', ''), entry.get('name', '')
        if pattern:
            a, b = pattern.casefold(), part.pattern.casefold()
            if not (a in b if entry.get('pattern_match') == 'contains' else a == b):
                continue
        if name and name.casefold() != part.name.casefold():
            continue
        priority = 3 if pattern and name else 2 if name else 1
        matches.append((priority, index, entry))
    return max(matches, default=(0, -1, None), key=lambda row: row[:2])[1:]


def rotate(x, y, angle):
    c, s = math.cos(angle), math.sin(angle)
    return x*c-y*s, x*s+y*c


def number(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError('Coordinates must be finite.')
    return value


def decimal(value):
    return f'{value:.8f}'.rstrip('0').rstrip('.') or '0'


def refs(text):
    result = [r.upper() for r in re.split(r'[,\s]+', text.strip()) if r]
    if len(result) != len(set(result)):
        raise ValueError('A group cannot contain the same RefDes twice.')
    return result


def hull(points):
    points = sorted(set(points))
    def cross(a, b, c):
        return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
    low, high = [], []
    for p in points:
        while len(low) >= 2 and cross(low[-2], low[-1], p) <= 0:
            low.pop()
        low.append(p)
    for p in reversed(points):
        while len(high) >= 2 and cross(high[-2], high[-1], p) <= 0:
            high.pop()
        high.append(p)
    return low[:-1]+high[:-1] if len(points) > 1 else points


def polygon_gap(a, b):
    """Reference viewer's exact convex-polygon edge clearance, in mm."""
    separated = False
    for polygon in (a, b):
        for i, p in enumerate(polygon):
            q = polygon[(i+1) % len(polygon)]
            nx, ny = -(q[1]-p[1]), q[0]-p[0]
            pa, pb = [[x*nx+y*ny for x, y in poly] for poly in (a, b)]
            if max(pa) < min(pb) or max(pb) < min(pa):
                separated = True
    if not separated:
        return 0.0
    def distance(p, u, v):
        dx, dy = v[0]-u[0], v[1]-u[1]
        length = dx*dx+dy*dy
        t = max(0, min(1, ((p[0]-u[0])*dx+(p[1]-u[1])*dy)/length)) if length else 0
        return math.hypot(p[0]-u[0]-t*dx, p[1]-u[1]-t*dy)
    return min(distance(p, poly[i], poly[(i+1) % len(poly)])
               for points, poly in ((a, b), (b, a)) for p in points for i in range(len(poly)))


def arc_points(a, b, c, steps=24):
    """Sample an arc through three points, including the control point."""
    ax, ay = a; bx, by = b; cx, cy = c
    d = 2*(ax*(by-cy)+bx*(cy-ay)+cx*(ay-by))
    if abs(d) < 1e-12:
        return [a, b, c]
    u = ((ax*ax+ay*ay)*(by-cy)+(bx*bx+by*by)*(cy-ay)+(cx*cx+cy*cy)*(ay-by))/d
    v = ((ax*ax+ay*ay)*(cx-bx)+(bx*bx+by*by)*(ax-cx)+(cx*cx+cy*cy)*(bx-ax))/d
    start, mid, end = [math.atan2(y-v, x-u) for x, y in (a, b, c)]
    span = (end-start) % math.tau
    if (mid-start) % math.tau > span:
        span -= math.tau
    radius = math.hypot(ax-u, ay-v)
    return [(u+radius*math.cos(start+span*i/steps), v+radius*math.sin(start+span*i/steps)) for i in range(steps+1)]


@dataclass
class Part:
    ref: str
    id: str
    name: str
    pattern: str
    x: float
    y: float
    angle: float
    side: str
    flip: bool
    element: object
    footprint: object
    via: bool = False


@dataclass
class Routing:
    # Selected edges between consecutive points, keyed by (net, trace).
    segments: dict
    vias: set
    allowed_pads: set | None = None
    embedded: set = field(default_factory=set)


class Board:
    def __init__(self, path=None, data=None):
        self.path = Path(path).resolve() if path else None
        self.data = data if data is not None else self.path.read_bytes()
        self.fingerprint = hashlib.sha256(self.data).hexdigest()
        parser = ET.XMLParser(remove_blank_text=False, resolve_entities=False, no_network=True)
        self.root = ET.fromstring(self.data, parser)
        if self.root.get('Type') != 'DipTrace-PCB':
            raise ValueError('Expected a DipTrace PCB XML (.dipxml), not a schematic.')
        self.factor = {'mm': 1, 'mil': .0254, 'inch': 25.4, 'in': 25.4}.get(self.root.get('Units'))
        if self.factor is None:
            raise ValueError('Unsupported PCB coordinate units.')
        self.board = self.root.find('Board')
        if self.board is None:
            raise ValueError('PCB has no Board element.')
        patterns = {p.get('PatternStyle'): p for p in self.root.iter('Pattern') if p.get('PatternStyle')}
        self.parts, self.by_id = {}, {}
        for e in self.board.findall('./Components/Component'):
            if e.get('X') is None:
                continue
            ref = e.findtext('RefDes', '').upper()
            if not ref or ref in self.parts or e.get('Id') in self.by_id:
                raise ValueError(f'Missing/duplicate placed component identity: {ref}')
            footprint = patterns.get(e.get('PatternStyle'))
            p = Part(ref, e.get('Id'), e.findtext('Name', ''), footprint.findtext('Name', '') if footprint is not None else '',
                     number(e.get('X'))*self.factor, number(e.get('Y'))*self.factor,
                     number(e.get('Angle', 0)), e.get('Side', 'Top'), e.get('Flip') == 'Y', e, footprint, e.get('Type') == 'Via')
            self.parts[ref] = p; self.by_id[p.id] = p
        self.pad_styles = {p.get('Name'): p for p in self.root.iter('PadStyle')}
        self.via_sizes = {v.get('Id'): number(v.get('Size', 0))*self.factor for v in self.board.findall('./ViaStyles/ViaStyle')}
        self.layers = {e.get('Id'): e.findtext('Name', 'Layer '+e.get('Id')) for e in self.board.findall('./CopperLayers/Lay')}
        self.nets = {e.get('Id'): e for e in self.board.findall('./Nets/Net')}
        self.traces = {(n, t.get('Id')): t for n, net in self.nets.items() for t in net.findall('./Traces/Trace')}
        for t in self.traces.values():
            for point in t.findall('./Points/Point'):
                self.layers.setdefault(point.get('Lay'), 'Layer '+point.get('Lay', '?'))

    def local_to_board(self, part, point):
        x, y = point
        if part.flip:
            x = -x
        x, y = rotate(x, y, part.angle)
        return part.x+x, part.y+y

    def pads(self, part):
        result = {}
        if part.via:
            return {'1': (part.x, part.y)}
        if part.footprint is not None:
            for e in part.footprint.findall('./Pads/Pad'):
                result[e.get('Id')] = self.local_to_board(part, (number(e.get('X', 0))*self.factor, number(e.get('Y', 0))*self.factor))
        return result

    def pad_polygons(self, part):
        result = []
        if part.footprint is None:
            return result
        for e in part.footprint.findall('./Pads/Pad'):
            style = self.pad_styles.get(e.get('Style'))
            stack = style.find('MainStack') if style is not None else None
            if stack is None:
                continue
            width, height = number(stack.get('Width', 0))*self.factor, number(stack.get('Height', 0))*self.factor
            x, y = number(e.get('X', 0))*self.factor, number(e.get('Y', 0))*self.factor
            angle = number(e.get('Angle', 0))
            ellipse = stack.get('Shape') == 'Ellipse'
            points = [(width/2*math.cos(i*math.tau/32), height/2*math.sin(i*math.tau/32)) for i in range(32)] if ellipse else [(-width/2,-height/2),(width/2,-height/2),(width/2,height/2),(-width/2,height/2)]
            ox, oy = number(stack.get('XOff', 0))*self.factor, number(stack.get('YOff', 0))*self.factor
            poly = []
            for px, py in points:
                px, py = rotate(px+ox, py+oy, angle)
                poly.append(self.local_to_board(part, (x+px, y+py)))
            result.append((e.get('Id'), poly))
        return result

    def geometry(self, part, sizes, extra_rotation=0):
        index, entry = size_match(part, sizes)
        paths = []
        if entry:
            w, h = number(entry['width_mm']), number(entry['height_mm'])
            angle = part.angle+math.radians(number(entry.get('rotation_deg', 0))+extra_rotation)
            poly = [rotate(x, y, angle) for x, y in [(-w/2,-h/2),(w/2,-h/2),(w/2,h/2),(-w/2,h/2)]]
            poly = [(part.x+x, part.y+y) for x, y in poly]
            return [(poly, True)], poly, 'Size database', index
        shapes = part.footprint.findall('./Shapes/Shape') if part.footprint is not None else []
        outlines = [s for s in shapes if 'outline' in s.get('Layer', '').casefold()]
        shapes = outlines or [s for s in shapes if 'silk' in s.get('Layer', '').casefold()]
        for s in shapes:
            kind = s.get('Type')
            points = [(number(p.get('X', 0))*self.factor, number(p.get('Y', 0))*self.factor) for p in s.findall('./Points/Item')]
            closed = False
            if kind in ('Rectangle', 'FillRect') and len(points) == 2:
                (a,b),(c,d) = points; points = [(a,b),(c,b),(c,d),(a,d)]; closed = True
            elif kind in ('Obround', 'FillObround', 'Ellipse', 'FillEllipse') and len(points) == 2:
                (a,b),(c,d) = points
                cx, cy = (a+c)/2, (b+d)/2; w,h = abs(c-a),abs(d-b)
                if 'Obround' in kind:
                    radius = min(w,h)/2
                    if w >= h:
                        points = [(cx+(w/2-radius)*(1 if math.cos(t)>=0 else -1)+radius*math.cos(t),cy+radius*math.sin(t)) for t in [i*math.tau/40 for i in range(40)]]
                    else:
                        points = [(cx+radius*math.cos(t),cy+(h/2-radius)*(1 if math.sin(t)>=0 else -1)+radius*math.sin(t)) for t in [i*math.tau/40 for i in range(40)]]
                else:
                    points = [(cx+w/2*math.cos(i*math.tau/40),cy+h/2*math.sin(i*math.tau/40)) for i in range(40)]
                closed = True
            elif kind == 'Arc' and len(points) == 3:
                points = arc_points(*points)
            elif kind not in ('Line', 'Polyline'):
                continue
            if len(points) < 2:
                continue
            points = [rotate(x,y,math.radians(extra_rotation)) for x,y in points]
            paths.append(([self.local_to_board(part, p) for p in points], closed))
        polygon = hull([p for line,_ in paths for p in line])
        return paths, polygon if len(polygon) >= 3 else [], 'Component outline' if outlines else 'Silkscreen' if paths else 'No body geometry', -1

    def routing(self, group, include_tracks, include_vias, layers):
        """Only copper on paths between selected pads; omit all external branches."""
        ids = {self.parts[r].id for r in group}
        if not include_tracks and not include_vias:
            return Routing({}, set())
        adjacency = defaultdict(set); edge_data = defaultdict(list); terminals = set()
        def edge(a,b,kind):
            if a == b:
                return
            adjacency[a].add(b); adjacency[b].add(a)
            edge_data[frozenset((a,b))].append(kind)
        def pad_node(cid, pid):
            part = self.by_id.get(cid)
            if part is None or (cid not in ids and not (part.via and include_vias)):
                return None
            node = ('pad',cid,pid)
            if cid in ids:
                terminals.add(node)
            return node
        for (nid,tid), trace in self.traces.items():
            points = trace.findall('./Points/Point')
            def point_node(i,layer):
                return ('point',nid,tid,points[i].get('Id',str(i)),layer)
            for i in range(len(points)-1):
                p,q = points[i:i+2]
                layer = q.get('Lay')  # Width/layer describe the incoming segment.
                if layer in layers:
                    edge(point_node(i,layer),point_node(i+1,layer),('segment',nid,tid,i))
                if i > 0 and include_vias and p.get('Lay') != layer and p.get('Lay') in layers and layer in layers:
                    edge(point_node(i,p.get('Lay')),point_node(i,layer),('embedded',nid,tid,i))
            if len(points) < 2:
                continue
            for side,index in [('1',0),('2',len(points)-1)]:
                layer = points[1].get('Lay') if side == '1' else points[-1].get('Lay')
                endpoint = point_node(index,layer)
                if layer not in layers:
                    continue
                kind = trace.get('Connected'+side)
                if kind == 'Pad':
                    node = pad_node(trace.get('Object'+side),trace.get('SubObject'+side))
                    if node is not None:
                        edge(endpoint,node,('attach',))
                elif kind == 'Trace':
                    target = self.traces.get((nid,trace.get('SubObject'+side)))
                    if target is not None:
                        candidates = target.findall('./Points/Point')
                        target_point = trace.get('Point'+side)
                        if target_point == '-1' and candidates:
                            target_point = candidates[0].get('Id','0')
                        match = next((p for p in candidates if p.get('Id') == target_point),None)
                        if match is not None:
                            edge(endpoint,('point',nid,target.get('Id'),target_point,layer),('attach',))
        chosen = terminal_paths(adjacency, terminals)
        segments = defaultdict(set); via_ids = set(); embedded = set()
        for pair in chosen:
            for node in pair:
                if node[0] == 'pad' and self.by_id[node[1]].via:
                    via_ids.add(node[1])
            for kind in edge_data[pair]:
                if kind[0] == 'segment':
                    segments[kind[1:3]].add(kind[3])
                elif kind[0] == 'embedded':
                    embedded.add(kind[1:])
        # Vias may be displayed/copied without tracks, but only if internally connected.
        return Routing(dict(segments), via_ids if include_vias else set(), ids | via_ids,embedded)


def terminal_paths(adjacency, terminals):
    """Union of simple terminal-to-terminal paths, using a block-cut forest.

    Unlike leaf pruning alone this also excludes a dangling loop. Iterative
    Tarjan traversal avoids recursion limits on large PCB routes.
    """
    discovery, low, parent, blocks = {}, {}, {}, []
    edge_stack = []; clock = 0
    for root in list(adjacency):
        if root in discovery:
            continue
        clock += 1; discovery[root] = low[root] = clock; parent[root] = None
        stack = [(root,iter(adjacency[root]))]
        while stack:
            node,it = stack[-1]
            other = next(it,None)
            if other is None:
                stack.pop(); p = parent[node]
                if p is not None:
                    low[p] = min(low[p],low[node])
                    if low[node] >= discovery[p]:
                        block = set()
                        while edge_stack:
                            pair = edge_stack.pop(); block.add(frozenset(pair))
                            if pair == (p,node):
                                break
                        blocks.append(block)
                continue
            if other not in discovery:
                parent[other] = node; clock += 1
                discovery[other] = low[other] = clock
                edge_stack.append((node,other)); stack.append((other,iter(adjacency[other])))
            elif other != parent[node] and discovery[other] < discovery[node]:
                low[node] = min(low[node],discovery[other]); edge_stack.append((node,other))
    forest = defaultdict(set)
    for index, block in enumerate(blocks):
        bnode = ('block',index)
        for vertex in set().union(*block):
            forest[bnode].add(vertex); forest[vertex].add(bnode)
    remaining = set(forest)
    queue = deque(n for n in forest if len(forest[n]) <= 1 and n not in terminals)
    degrees = {n:len(neighbors) for n,neighbors in forest.items()}
    while queue:
        node = queue.popleft()
        if node not in remaining:
            continue
        remaining.remove(node)
        for neighbor in forest[node] & remaining:
            degrees[neighbor] -= 1
            if degrees[neighbor] <= 1 and neighbor not in terminals:
                queue.append(neighbor)
    return set().union(*(block for i,block in enumerate(blocks) if ('block',i) in remaining)) if blocks else set()


def default_recipe(board):
    defaults = json.loads(DEFAULTS_PATH.read_text(encoding='utf-8-sig'))
    if defaults.get('schema_version') != 1 or not isinstance(defaults.get('recipe'),dict):
        raise ValueError('Project defaults require schema_version 1 and a recipe object.')
    recipe = deepcopy(defaults['recipe']); recipe.update(schema_version=1,pcb=str(board.path))
    if recipe['view'].get('layers') is None:
        recipe['view']['layers'] = list(board.layers)
    return recipe


def group_refs(board, recipe):
    groups = recipe['groups'] or [{'name':'Whole PCB','refs':[p.ref for p in board.parts.values() if not p.via]}]
    used = set(); result = []
    for group in groups:
        names = refs(group['refs']) if isinstance(group['refs'],str) else refs(','.join(group['refs']))
        if not names:
            raise ValueError('Each group needs at least one RefDes.')
        missing = [r for r in names if r not in board.parts or board.parts[r].via]
        if missing:
            raise ValueError('Unknown part RefDes: '+', '.join(missing))
        if used.intersection(names):
            raise ValueError('A part can belong to only one group.')
        used.update(names); result.append(names)
    return result


def transform_for(settings, index, total):
    layout = settings['layout']
    if layout == 'Linear':
        return (number(settings['dx'])*index,number(settings['dy'])*index,0,0,0)
    if layout == 'Grid':
        columns, rows = int(settings['columns']),int(settings['rows'])
        if columns < 1 or rows < 1 or total > columns*rows:
            raise ValueError('Grid dimensions must accommodate every instance.')
        x,y = index % columns,index // columns
        return (x*number(settings['column_dx'])+y*number(settings['row_dx']),x*number(settings['column_dy'])+y*number(settings['row_dy']),0,0,0)
    angle = math.tau/total if settings['automatic_angle'] else math.radians(number(settings['angle_deg']))
    return (0,0,angle*index,number(settings['center_x']),number(settings['center_y']))


def transform_point(point, transform):
    dx,dy,angle,cx,cy = transform
    x,y = rotate(point[0]-cx,point[1]-cy,angle)
    return x+cx+dx,y+cy+dy


def intervals(edges):
    result = []
    for index in sorted(edges):
        if result and result[-1][1] == index:
            result[-1] = (result[-1][0],index+1)
        else:
            result.append((index,index+1))
    return result


def move_component(element, board, transform):
    x,y = transform_point((number(element.get('X'))*board.factor,number(element.get('Y'))*board.factor),transform)
    element.set('X',decimal(x/board.factor)); element.set('Y',decimal(y/board.factor))
    if element.get('Type') != 'Via':
        element.set('Angle',decimal((number(element.get('Angle',0))+transform[2]) % math.tau))


def routing_fragments(board, routing):
    """Extract selected spans and reconstruct boundary endpoints at junctions."""
    fragments = []; lookup = defaultdict(list)
    for key,edges in routing.segments.items():
        points = board.traces[key].findall('./Points/Point')
        for start,end in intervals(edges):
            fragment = deepcopy(board.traces[key]); container = fragment.find('Points')
            for child in list(container):
                container.remove(child)
            for point in points[start:end+1]:
                container.append(deepcopy(point))
            if len(container) >= 2:
                container[0].set('Lay',container[1].get('Lay'))
            fragment.set('PairSeparateTrace','-1')
            index = len(fragments)
            fragments.append((key,start,end,fragment))
            for point in points[start:end+1]:
                lookup[(key[0],key[1],point.get('Id'))].append(index)
    for index,(key,start,end,fragment) in enumerate(fragments):
        original = board.traces[key]; points = original.findall('./Points/Point')
        for side,pos in [('1',start),('2',end)]:
            full_end = (side == '1' and pos == 0) or (side == '2' and pos == len(points)-1)
            connected = original.get('Connected'+side) if full_end else None
            if connected == 'Pad':
                cid = original.get('Object'+side)
                if routing.allowed_pads is None or cid in routing.allowed_pads:
                    fragment.set('_pad'+side,cid)
                    continue
            target = None
            if connected == 'Trace':
                pid = original.get('Point'+side)
                other_key = (key[0],original.get('SubObject'+side))
                if pid == '-1' and other_key in board.traces:
                    pid = board.traces[other_key].find('./Points/Point').get('Id')
                choices = lookup.get((*other_key,pid),[])
                if choices:
                    target = (choices[0],pid)
            if target is None:
                pid = points[pos].get('Id')
                choices = [i for i in lookup.get((*key,pid),[]) if i != index]
                if choices:
                    target = (choices[0],pid)
            if target:
                fragment.set('_trace'+side,str(target[0])); fragment.set('Point'+side,target[1])
            else:
                fragment.set('Connected'+side,'Free'); fragment.set('Object'+side,'-1'); fragment.set('SubObject'+side,'-1'); fragment.set('Point'+side,'-1')
    # A junction's original carrier may be entirely external. Join the retained
    # local branches to one another instead of retaining that external carrier.
    locations = defaultdict(list)
    for index,(key,start,end,fragment) in enumerate(fragments):
        points = fragment.findall('./Points/Point')
        for i,p in enumerate(points):
            locations[(key[0],number(p.get('X')),number(p.get('Y')),p.get('Lay'))].append((index,p.get('Id'),0 < i < len(points)-1))
    for index,(key,start,end,fragment) in enumerate(fragments):
        points = fragment.findall('./Points/Point')
        for side,p in [('1',points[0]),('2',points[-1])]:
            if fragment.get('Connected'+side) != 'Free':
                continue
            candidates = [(j,pid) for j,pid,inside in locations[(key[0],number(p.get('X')),number(p.get('Y')),p.get('Lay'))] if j != index and (inside or j < index)]
            if candidates:
                j,pid = candidates[0]; fragment.set('_trace'+side,str(j)); fragment.set('Point'+side,pid)
    return fragments


def remove_routing(board, routing):
    """Retain external spans and repair trace references after local replacement."""
    complement = {}
    for key, chosen in routing.segments.items():
        all_edges = set(range(len(board.traces[key].findall('./Points/Point'))-1))
        complement[key] = all_edges-chosen
    remaining = routing_fragments(board,Routing(complement,set()))
    ids = {}; lookup = {}; next_ids = {n:max([int(t.get('Id')) for t in net.findall('./Traces/Trace')]+[-1])+1 for n,net in board.nets.items()}
    used = set()
    for i,(key,start,end,t) in enumerate(remaining):
        if key not in used:
            ids[i] = key[1]; used.add(key)
        else:
            ids[i] = str(next_ids[key[0]]); next_ids[key[0]] += 1
        for p in t.findall('./Points/Point'):
            lookup[(key[0],key[1],p.get('Id'))] = ids[i]
    for i,(key,start,end,t) in enumerate(remaining):
        t.set('Id',ids[i])
        for side in ('1','2'):
            if t.get('_pad'+side) is not None:
                del t.attrib['_pad'+side]
            if t.get('_trace'+side) is not None:
                target = int(t.get('_trace'+side)); del t.attrib['_trace'+side]
                t.set('Connected'+side,'Trace'); t.set('Object'+side,key[0]); t.set('SubObject'+side,ids[target])
        board.nets[key[0]].find('Traces').append(t)
    for key in routing.segments:
        trace = board.traces[key]; trace.getparent().remove(trace)
    # Existing branches into a split trace follow the retained span containing their junction.
    for nid,net in board.nets.items():
        for t in net.findall('./Traces/Trace'):
            if t in [row[3] for row in remaining]:
                continue
            for side in ('1','2'):
                old_key = (nid,t.get('SubObject'+side))
                if t.get('Connected'+side) == 'Trace' and old_key in routing.segments:
                    new_id = lookup.get((*old_key,t.get('Point'+side)))
                    if new_id is None:
                        t.set('Connected'+side,'Free'); t.set('Object'+side,'-1'); t.set('SubObject'+side,'-1'); t.set('Point'+side,'-1')
                    else:
                        t.set('SubObject'+side,new_id)


def validate_board(board, original=None):
    refs_seen,ids_seen = set(),set()
    for p in board.parts.values():
        if p.ref in refs_seen or p.id in ids_seen:
            raise ValueError('Duplicate component identity.')
        refs_seen.add(p.ref); ids_seen.add(p.id)
        for pad in p.element.findall('./Pads/Pad'):
            nid = pad.get('NetId')
            if nid not in (None,'-1'):
                net = board.nets.get(nid)
                if net is None or not any(i.get('Comp') == p.id and i.get('Pad') == pad.get('Id') for i in net.findall('./Pads/Item')):
                    old = original.by_id.get(p.id) if original else None
                    if old is not None and any(x.attrib == pad.attrib for x in old.element.findall('./Pads/Pad')):
                        continue  # Do not reject an untouched pre-existing export inconsistency.
                    raise ValueError(f'{p.ref}: pad is missing from its net.')
    for (nid,tid),t in board.traces.items():
        for side in ('1','2'):
            if t.get('Connected'+side) == 'Pad':
                p = board.by_id.get(t.get('Object'+side))
                if p is None or not any(pad.get('Id') == t.get('SubObject'+side) and pad.get('NetId') == nid for pad in p.element.findall('./Pads/Pad')):
                    raise ValueError(f'Invalid pad connection in net {nid}, trace {tid}.')
            elif t.get('Connected'+side) == 'Trace':
                other = board.traces.get((nid,t.get('SubObject'+side)))
                if t.get('Object'+side) != nid or other is None:
                    raise ValueError(f'Invalid trace connection in net {nid}, trace {tid}.')
                if t.get('Point'+side) not in ('-1',None) and not any(p.get('Id') == t.get('Point'+side) for p in other.findall('./Points/Point')):
                    raise ValueError('Trace junction point is missing.')


def replicate(source, recipe, cancelled=lambda: False, progress=lambda current,total: None):
    """Return a validated proposed PCB and its groups; never writes input files."""
    groups = group_refs(source,recipe); settings = recipe['replication']
    source_index = int(settings['source'])
    if not 0 <= source_index < len(groups):
        raise ValueError('Select a valid source group.')
    source_refs = groups[source_index]
    pad_counts = defaultdict(int)
    for ref in source_refs:
        for pad in source.parts[ref].element.findall('./Pads/Pad'):
            pad_counts[pad.get('NetId')] += 1
    internal_nets = {nid for nid,count in pad_counts.items() if count >= 2}
    ordered = [source_refs]+[g for i,g in enumerate(groups) if i != source_index]
    clone = settings['mode'] == 'Create copies'
    total = int(settings['count']) if clone else len(ordered)
    if clone and settings['layout'] == 'Grid':
        total = int(settings['columns'])*int(settings['rows'])
    if not 2 <= total <= 1000:
        raise ValueError('Replication needs 2–1000 total instances, including the source.')
    layers = set(recipe['view']['layers']); tracks = recipe['view']['tracks']; vias = recipe['view']['vias']
    routes = source.routing(source_refs,tracks,vias,layers)
    fragments = routing_fragments(source,routes) if tracks else []
    result = Board(data=ET.tostring(source.root,encoding='utf-8',xml_declaration=True))
    components = result.board.find('Components'); nets_container = result.board.find('Nets')
    next_component = max([int(p.id) for p in result.parts.values()]+[-1])+1
    next_net = max([int(n) for n in result.nets]+[-1])+1
    occupied_refs = set(result.parts); occupied_names = {n.findtext('Name','') for n in result.nets.values()}
    prefix_numbers = defaultdict(lambda:1)
    next_name = 1
    generated = [{'name':recipe['groups'][source_index].get('name','Source') if recipe['groups'] else 'Source','refs':source_refs}]
    generated_routes = [routes]
    embedded = []
    if vias and not tracks:
        for nid,tid,i in routes.embedded:
            points = source.traces[nid,tid].findall('./Points/Point')
            point = points[i]
            e = ET.Element('Component',Id=f'embedded-{nid}-{tid}-{i}',UpdateId='-1',Type='Via',ViaStyle=point.get('ViaStyle','0'),X=point.get('X'),Y=point.get('Y'))
            ET.SubElement(e,'RefDes').text = 'STATICVIA0'; ET.SubElement(e,'Name').text = 'Static Via'
            ET.SubElement(ET.SubElement(e,'Pads'),'Pad',Id='1',NetId=nid)
            embedded.append(Part('STATICVIA0',e.get('Id'),'Static Via','',number(e.get('X'))*source.factor,number(e.get('Y'))*source.factor,0,'Top',False,e,None,True))
    for instance in range(1,total):
        if cancelled():
            raise InterruptedError('Preview cancelled.')
        transform = transform_for(settings,instance,total)
        if not clone:
            target_index = [i for i in range(len(groups)) if i != source_index][instance-1]
            target_transform = transform_for(settings,target_index,total)
            source_transform = transform_for(settings,source_index,total)
            transform = (target_transform[0]-source_transform[0],target_transform[1]-source_transform[1],target_transform[2]-source_transform[2],target_transform[3],target_transform[4])
        cmap, nmap, target_refs = {}, {}, []
        source_parts = [source.parts[r] for r in source_refs]
        if not clone:
            target_refs = ordered[instance]
            if len(target_refs) != len(source_refs):
                raise ValueError('Existing groups must have the same number of parts, in corresponding order.')
            reverse_nets = {}
            for a,ref in zip(source_parts,target_refs):
                b = result.parts[ref]
                if a.pattern.casefold() != b.pattern.casefold() or source.pads(a).keys() != result.pads(b).keys():
                    raise ValueError(f'Incompatible corresponding parts: {a.ref} / {b.ref}. Check list order.')
                cmap[a.id] = b.id
                ap = {p.get('Id'):p.get('NetId') for p in a.element.findall('./Pads/Pad')}
                bp = {p.get('Id'):p.get('NetId') for p in b.element.findall('./Pads/Pad')}
                for pid,nid in ap.items():
                    if nid in (None,'-1'):
                        continue
                    dest = bp.get(pid)
                    if dest not in result.nets or (nid in nmap and nmap[nid] != dest) or (dest in reverse_nets and reverse_nets[dest] != nid):
                        raise ValueError(f'Inconsistent destination net mapping: {a.ref} / {b.ref}.')
                    nmap[nid] = dest; reverse_nets[dest] = nid
            old_routes = result.routing(target_refs,tracks,vias,layers)
            if tracks:
                protected = result.routing(source_refs,tracks,vias,layers)
                if any(edges & protected.segments.get(key,set()) for key,edges in old_routes.segments.items()):
                    raise ValueError('Source and destination groups share selected copper. Narrow the groups/layers, or arrange parts with Tracks off.')
                remove_routing(result,old_routes)
            # Remove only unused private vias. Shared external vias stay in place.
            remaining_refs = {t.get('Object'+s) for n in result.nets.values() for t in n.findall('./Traces/Trace') for s in ('1','2') if t.get('Connected'+s) == 'Pad'}
            for vid in old_routes.vias-remaining_refs:
                v = result.by_id[vid]; components.remove(v.element)
                for n in result.nets.values():
                    for pad in list(n.findall('./Pads/Item')):
                        if pad.get('Comp') == vid:
                            pad.getparent().remove(pad)
        source_objects = source_parts+[source.by_id[v] for v in sorted(routes.vias)]+embedded
        for part in source_objects:
            if not clone and not part.via:
                target = result.by_id[cmap[part.id]].element
                target.set('X',part.element.get('X')); target.set('Y',part.element.get('Y'))
                target.set('Angle',part.element.get('Angle','0')); target.set('Side',part.side)
                if part.flip:
                    target.set('Flip','Y')
                elif 'Flip' in target.attrib:
                    del target.attrib['Flip']
                move_component(target,result,transform)
                continue
            copied = deepcopy(part.element)
            cid = str(next_component); next_component += 1; cmap[part.id] = cid
            prefix = re.match(r'[A-Za-z]+',part.ref)
            prefix = prefix.group() if prefix else 'U'
            index = prefix_numbers[prefix]
            while prefix+str(index) in occupied_refs:
                index += 1
            ref = prefix+str(index); occupied_refs.add(ref)
            prefix_numbers[prefix] = index+1
            copied.set('Id',cid); copied.set('UpdateId','-1'); copied.find('RefDes').text = ref
            copied.attrib.pop('Group',None)
            for pad in copied.findall('./Pads/Pad'):
                nid = pad.get('NetId')
                if nid in (None,'-1'):
                    continue
                if clone and nid not in internal_nets:
                    pad.set('NetId','-1')
                    continue
                if nid not in nmap:
                    if not clone:
                        raise ValueError('Via net could not be mapped to the destination group.')
                    new_net = deepcopy(source.nets[nid]); new_nid = str(next_net); next_net += 1
                    while 'Net '+str(next_name) in occupied_names:
                        next_name += 1
                    new_net.find('Name').text = 'Net '+str(next_name); occupied_names.add('Net '+str(next_name)); next_name += 1
                    new_net.set('Id',new_nid); new_net.set('UpdateId','-1')
                    # New nets carry settings, never original external pads or copper.
                    for child in list(new_net):
                        if child.tag not in ('Name',):
                            new_net.remove(child)
                    ET.SubElement(new_net,'Pads'); ET.SubElement(new_net,'Traces')
                    nets_container.append(new_net); result.nets[new_nid] = new_net; nmap[nid] = new_nid
                pad.set('NetId',nmap[nid])
                net = result.nets[nmap[nid]]
                pads = net.find('Pads')
                if pads is None:
                    pads = ET.SubElement(net,'Pads')
                ET.SubElement(pads,'Item',Comp=cid,Pad=pad.get('Id'))
            move_component(copied,result,transform); components.append(copied)
            if not part.via:
                target_refs.append(ref)
        trace_ids = {}; counters = {}
        for i,(key,start,end,fragment) in enumerate(fragments):
            nid = nmap[key[0]]
            if nid not in counters:
                counters[nid] = max([int(t.get('Id')) for t in result.nets[nid].findall('./Traces/Trace')]+[-1])+1
            trace_ids[i] = str(counters[nid]); counters[nid] += 1
        for i,(key,start,end,fragment) in enumerate(fragments):
            t = deepcopy(fragment); nid = nmap[key[0]]; t.set('Id',trace_ids[i])
            for side in ('1','2'):
                if t.get('_pad'+side) is not None:
                    cid = t.get('_pad'+side); del t.attrib['_pad'+side]
                    if cid not in cmap:
                        raise ValueError('Internal routing unexpectedly reaches an external part.')
                    t.set('Connected'+side,'Pad'); t.set('Object'+side,cmap[cid])
                if t.get('_trace'+side) is not None:
                    j = int(t.get('_trace'+side)); del t.attrib['_trace'+side]
                    t.set('Connected'+side,'Trace'); t.set('Object'+side,nid); t.set('SubObject'+side,trace_ids[j])
            for p in t.findall('./Points/Point'):
                x,y = transform_point((number(p.get('X'))*result.factor,number(p.get('Y'))*result.factor),transform)
                p.set('X',decimal(x/result.factor)); p.set('Y',decimal(y/result.factor))
                p.set('PairPoint','-1')
                if p.get('MeanderAngle') is not None:
                    p.set('MeanderAngle',decimal(number(p.get('MeanderAngle'))+transform[2]))
            container = result.nets[nid].find('Traces')
            if container is None:
                container = ET.SubElement(result.nets[nid],'Traces')
            container.append(t)
        new_segments = {}
        new_embedded = set()
        for i,(key,start,end,fragment) in enumerate(fragments):
            new_segments[nmap[key[0]],trace_ids[i]] = set(range(end-start))
            for original_index in range(start+1,end):
                if (*key,original_index) in routes.embedded:
                    new_embedded.add((nmap[key[0]],trace_ids[i],original_index-start))
        generated_routes.append(Routing(new_segments,{cmap[p.id] for p in source_objects if p.via},embedded=new_embedded))
        name = f'Group {instance+1}'
        if not clone:
            target_index = [i for i in range(len(groups)) if i != source_index][instance-1]
            name = recipe['groups'][target_index].get('name',name)
        generated.append({'name':name,'refs':target_refs})
        # Existing destinations need fresh geometry; creation can parse once.
        if not clone:
            result = Board(data=ET.tostring(result.root,encoding='utf-8',xml_declaration=True))
            components = result.board.find('Components'); nets_container = result.board.find('Nets')
        progress(instance,total-1)
    if clone:
        result = Board(data=ET.tostring(result.root,encoding='utf-8',xml_declaration=True))
    if not clone:
        target_ids = {result.parts[r].id for g in ordered[1:] for r in g}
        for ratline in list(result.board.findall('./Ratlines/Ratline')):
            if ratline.get('Comp1') in target_ids or ratline.get('Comp2') in target_ids:
                ratline.getparent().remove(ratline)
    validate_board(result,source)
    if not clone:
        order = [source_index]+[i for i in range(len(groups)) if i != source_index]
        generated = [generated[order.index(i)] for i in range(len(groups))]
        generated_routes = [generated_routes[order.index(i)] for i in range(len(groups))]
    result.replication_routing = generated_routes
    return result,generated
