"""The 3D designer's kit, imported inside Blender by a section's scene.py (`import blender_kit as k`). Everything in it
was worked out making a whole-frame 3D piece by hand (2026-10-30) and is tested: set it up, place the person in the
world, light it, move the camera, render. The designer writes the world and its things; the kit does the plumbing.

    import blender_kit as k
    k.begin()                       # the scene: size, fps, length, EEVEE, motion blur, bloom, the person's colours
    cam = k.camera([(0, (0.3, -14.6, 0.25)), (4.5, (0.25, -13.2, 0.2))], cuts=[(4.6, (2.1, -12.6, -0.7))])
    him = k.person(x=-2.55, z=-0.22)  # the person cut out, on a card facing the camera, faded into the world below
    ring, glow = k.emissive("ring", (0.25, 0.55, 1.0), 40)
    k.keys(glow.inputs[1], "default_value", [(0, 0), (1.2, 160), (1.7, 70)])
    k.end()                         # renders what was asked (stills for a look, or every frame)

Run (Flicello does): blender -b -P scene.py -- <desk folder> stills 0.8,2.0  |  render
The desk folder holds brief.json (duration, width, height, fps) and ../stage/person/p_0001.png... (the person's
frames with their cut-out as alpha) when there is a person."""
import json
import math
import os
import random
import re
import sys

import bpy

ARGS = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
DESK = os.path.abspath(ARGS[0]) if ARGS else os.getcwd()
MODE = ARGS[1] if len(ARGS) > 1 else "stills"
ASKED = ARGS[2] if len(ARGS) > 2 else ""
with open(os.path.join(DESK, "brief.json"), encoding="utf-8") as fh:
    BRIEF = json.load(fh)
FPS = int(BRIEF.get("fps") or 30)
W, H = int(BRIEF.get("width") or 1920), int(BRIEF.get("height") or 1080)
DUR = float(BRIEF["end"]) - float(BRIEF["start"])
N = max(1, int(round(DUR * FPS)))
STAGE = os.path.join(os.path.dirname(DESK), "stage")
CAM_DISTANCE = [14.0]  # where the camera starts, for sizing the person's card


def f(t):
    """Seconds into the section -> Blender frame."""
    return int(round(t * FPS)) + 1


def begin(samples=32, exposure=0.0, bloom=(1.4, 0.45, 0.55)):
    """A clean scene of the section's size and length: EEVEE, motion blur, the 'Standard' view (the person's footage
    keeps its own colours), a black world and bloom (threshold, size, strength)."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.render.fps = FPS
    sc.frame_start, sc.frame_end = 1, N
    sc.render.resolution_x, sc.render.resolution_y = W, H
    for eng in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
        try:
            sc.render.engine = eng
            break
        except TypeError:
            pass
    sc.eevee.taa_render_samples = samples
    sc.render.use_motion_blur = True
    try:
        sc.render.motion_blur_shutter = 0.5
    except AttributeError:
        pass
    sc.view_settings.view_transform = "Standard"
    sc.view_settings.look = "None"
    sc.view_settings.exposure = exposure
    sc.world = bpy.data.worlds.new("world")
    sc.world.use_nodes = True
    sc.world.node_tree.nodes["Background"].inputs[0].default_value = (0, 0, 0, 1)
    _bloom(*bloom)
    return sc


def _bloom(threshold, size, strength):
    sc = bpy.context.scene
    sc.use_nodes = True
    tree = sc.node_tree
    for nd in list(tree.nodes):
        tree.nodes.remove(nd)
    layers = tree.nodes.new("CompositorNodeRLayers")
    glare = tree.nodes.new("CompositorNodeGlare")
    for kind in ("BLOOM", "FOG_GLOW"):
        try:
            glare.glare_type = kind
            break
        except TypeError:
            pass
    for name, v in (("Threshold", threshold), ("Size", size), ("Strength", strength)):
        if name in glare.inputs:
            glare.inputs[name].default_value = v
    for attr, v in (("threshold", threshold), ("size", 7), ("quality", "HIGH")):
        try:
            setattr(glare, attr, v)
        except (AttributeError, TypeError):
            pass
    out = tree.nodes.new("CompositorNodeComposite")
    tree.links.new(layers.outputs["Image"], glare.inputs["Image"])
    tree.links.new(glare.outputs["Image"], out.inputs["Image"])


def keys(owner, path, points, interp="BEZIER"):
    """Keyframes [(seconds, value), ...] on owner.path (an object's location/scale/rotation_euler, a light's energy,
    a node input's default_value)."""
    for t, v in points:
        setattr(owner, path, v)
        owner.keyframe_insert(path, frame=f(t))
    anim = getattr(owner, "animation_data", None) or getattr(getattr(owner, "id_data", None), "animation_data", None)
    if anim and anim.action:
        for fc in anim.action.fcurves:
            for k in fc.keyframe_points:
                k.interpolation = interp


def obj(name, data=None, loc=(0, 0, 0)):
    o = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(o)
    o.location = loc
    return o


def camera(moves, cuts=(), lens=40, fstop=4.0, target=(0.35, 1.2, 0.15)):
    """The camera: `moves` [(seconds, (x, y, z)), ...] glide (it looks along +Y at `target`); each of `cuts`
    [(seconds, (x, y, z))] is a hard cut to a new angle at that second (no glide across it). Depth of field focused on
    the target."""
    data = bpy.data.cameras.new("cam")
    data.lens = lens
    data.dof.use_dof = True
    data.dof.aperture_fstop = fstop
    cam = obj("cam", data)
    bpy.context.scene.camera = cam
    aim = obj("aim", None, target)
    track = cam.constraints.new("TRACK_TO")
    track.target = aim
    track.track_axis, track.up_axis = "TRACK_NEGATIVE_Z", "UP_Y"
    data.dof.focus_object = aim
    points = sorted([(t, p) for t, p in moves] + [(t, p) for t, p in cuts], key=lambda x: x[0])
    CAM_DISTANCE[0] = abs(points[0][1][1]) if points else 14.0
    for t, p in points:
        cam.location = p
        cam.keyframe_insert("location", frame=f(t))
    cut_frames = {f(t) for t, _ in cuts}
    for fc in cam.animation_data.action.fcurves:
        pts = list(fc.keyframe_points)
        for i, k in enumerate(pts):
            k.interpolation = "BEZIER"
            if i + 1 < len(pts) and round(pts[i + 1].co[0]) in cut_frames:
                k.interpolation = "CONSTANT"  # hold, then jump: a cut
    return cam, aim


def frame_width(distance=None, lens=40):
    """The frame's width in scene units at that distance from the camera (36 mm sensor)."""
    d = distance if distance is not None else CAM_DISTANCE[0]
    return 2 * d * math.tan(math.atan(18 / lens))


def mat(name):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    return m, m.node_tree.nodes, m.node_tree.links


def person(x=-2.55, z=-0.22, y=0.0, scale=1.16, glow=0.68, light=0.35, contrast=0.22, fade_bottom=0.16, cool=0.94):
    """The person, cut out frame by frame, on a card facing the camera at depth y, sized to the frame (`scale`
    times its width at the camera's starting distance) and moved by (x, z). Their own picture shows as emission
    (`glow`, graded with `contrast` and a slight `cool` cast for a dark world) and the world's lights add on it
    (`light`); their body fades out at the bottom (`fade_bottom` of the card) so the camera can move freely.
    None when the film has no person (a voice film)."""
    first = os.path.join(STAGE, "person", "p_0001.png")
    if not os.path.exists(first):
        return None
    img = bpy.data.images.load(first)
    img.source = "SEQUENCE"
    bpy.ops.mesh.primitive_plane_add(size=1, location=(x, y, z))
    card = bpy.context.object
    card.name = "person"
    card.rotation_euler = (math.radians(90), 0, 0)
    fw = frame_width(CAM_DISTANCE[0] + y)
    card.scale = (fw * scale, fw * H / W * scale, 1)
    m, n, l = mat("person")
    n.clear()
    tex = n.new("ShaderNodeTexImage")
    tex.image = img
    tex.image_user.frame_duration = N
    tex.image_user.frame_start = 1
    tex.image_user.use_auto_refresh = True
    grade = n.new("ShaderNodeBrightContrast")
    grade.inputs["Contrast"].default_value = contrast
    tint = n.new("ShaderNodeMixRGB")
    tint.blend_type = "MULTIPLY"
    tint.inputs[0].default_value = 1.0
    tint.inputs[2].default_value = (cool, (1 + cool) / 2, 1.0, 1)
    em = n.new("ShaderNodeEmission")
    em.inputs[1].default_value = glow
    dim = n.new("ShaderNodeMixRGB")
    dim.blend_type = "MULTIPLY"
    dim.inputs[0].default_value = 1.0
    dim.inputs[2].default_value = (light, light, light, 1)
    dif = n.new("ShaderNodeBsdfDiffuse")
    add = n.new("ShaderNodeAddShader")
    clear = n.new("ShaderNodeBsdfTransparent")
    mix = n.new("ShaderNodeMixShader")
    out = n.new("ShaderNodeOutputMaterial")
    l.new(tex.outputs["Color"], grade.inputs["Color"])
    l.new(grade.outputs[0], tint.inputs[1])
    l.new(tint.outputs[0], em.inputs[0])
    l.new(tex.outputs["Color"], dim.inputs[1])
    l.new(dim.outputs[0], dif.inputs[0])
    l.new(em.outputs[0], add.inputs[0])
    l.new(dif.outputs[0], add.inputs[1])
    uv = n.new("ShaderNodeTexCoord")
    sep = n.new("ShaderNodeSeparateXYZ")
    l.new(uv.outputs["UV"], sep.inputs[0])
    low = n.new("ShaderNodeMapRange")
    low.inputs["From Min"].default_value, low.inputs["From Max"].default_value = 0.0, max(0.001, fade_bottom)
    top = n.new("ShaderNodeMapRange")
    top.inputs["From Min"].default_value, top.inputs["From Max"].default_value = 1.0, 0.985
    l.new(sep.outputs["Y"], low.inputs["Value"])
    l.new(sep.outputs["Y"], top.inputs["Value"])
    a1 = n.new("ShaderNodeMath")
    a1.operation = "MULTIPLY"
    a2 = n.new("ShaderNodeMath")
    a2.operation = "MULTIPLY"
    l.new(tex.outputs["Alpha"], a1.inputs[0])
    l.new(low.outputs["Result"], a1.inputs[1])
    l.new(a1.outputs[0], a2.inputs[0])
    l.new(top.outputs["Result"], a2.inputs[1])
    l.new(a2.outputs[0], mix.inputs[0])
    l.new(clear.outputs[0], mix.inputs[1])
    l.new(add.outputs[0], mix.inputs[2])
    l.new(mix.outputs[0], out.inputs[0])
    try:
        m.surface_render_method = "BLENDED"
    except AttributeError:
        m.blend_method = "BLEND"
    card.data.materials.append(m)
    return card


def emissive(name, rgb, strength):
    """A material that is light: (material, its Emission node: key `inputs[1]` 'default_value' to animate it)."""
    m, n, l = mat(name)
    n.clear()
    e = n.new("ShaderNodeEmission")
    e.inputs[0].default_value = (*rgb, 1)
    e.inputs[1].default_value = strength
    o = n.new("ShaderNodeOutputMaterial")
    l.new(e.outputs[0], o.inputs[0])
    return m, e


def lacquer(name, rgb, roughness=0.22, coat=1.0, glow=0.35):
    """Glossy painted lacquer (a toy, a tile), with a little of its own glow so it reads in a dark world."""
    m, n, _ = mat(name)
    b = n["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*rgb, 1)
    b.inputs["Roughness"].default_value = roughness
    for key in ("Coat Weight", "Clearcoat"):
        if key in b.inputs:
            b.inputs[key].default_value = coat
    for key in ("Emission Color", "Emission"):
        if key in b.inputs:
            b.inputs[key].default_value = (*rgb, 1)
            break
    if "Emission Strength" in b.inputs:
        b.inputs["Emission Strength"].default_value = glow
    return m


def glass(name, rgb=(0.6, 0.8, 1.0), roughness=0.03, ior=1.45):
    """Clear tinted glass (EEVEE ray tracing is switched on for it)."""
    sc = bpy.context.scene
    for attr in ("use_raytracing", "use_ssr", "use_ssr_refraction"):
        try:
            setattr(sc.eevee, attr, True)
        except AttributeError:
            pass
    m, n, _ = mat(name)
    for attr in ("use_raytrace_refraction", "use_screen_refraction"):
        try:
            setattr(m, attr, True)
        except AttributeError:
            pass
    b = n["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*rgb, 1)
    b.inputs["Roughness"].default_value = roughness
    b.inputs["IOR"].default_value = ior
    for key in ("Transmission Weight", "Transmission"):
        if key in b.inputs:
            b.inputs[key].default_value = 1.0
            break
    return m


def light(kind, loc, rgb=(1, 1, 1), energy=500.0, size=1.0, rot=(0, 0, 0), energies=None):
    """A light ('POINT', 'AREA', 'SPOT', 'SUN'); `energies` [(seconds, watts)] animates it."""
    data = bpy.data.lights.new(kind.lower(), kind)
    data.color = rgb
    data.energy = energy
    if kind == "AREA":
        data.size = size
    else:
        try:
            data.shadow_soft_size = size
        except AttributeError:
            pass
    o = obj(kind.lower(), data, loc)
    o.rotation_euler = rot
    if energies:
        keys(data, "energy", energies)
    return o, data


def backdrop(rgb=(0.004, 0.011, 0.04), at=(2.0, 22, 0.5), spread=0.035, wake=None):
    """A wide, faint glow far behind everything (depth in a dark world); `wake` (from, to) seconds fades it in."""
    bpy.ops.mesh.primitive_plane_add(size=60, location=at, rotation=(math.radians(90), 0, 0))
    plane = bpy.context.object
    m, n, l = mat("backdrop")
    n.clear()
    coord = n.new("ShaderNodeTexCoord")
    mp = n.new("ShaderNodeMapping")
    mp.inputs["Scale"].default_value = (spread, spread, spread)
    grad = n.new("ShaderNodeTexGradient")
    grad.gradient_type = "SPHERICAL"
    ramp = n.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = (0, 0, 0, 1)
    ramp.color_ramp.elements[1].color = (*rgb, 1)
    e = n.new("ShaderNodeEmission")
    o = n.new("ShaderNodeOutputMaterial")
    l.new(coord.outputs["Object"], mp.inputs[0])
    l.new(mp.outputs[0], grad.inputs[0])
    l.new(grad.outputs[0], ramp.inputs[0])
    l.new(ramp.outputs[0], e.inputs[0])
    l.new(e.outputs[0], o.inputs[0])
    plane.data.materials.append(m)
    if wake:
        keys(e.inputs[1], "default_value", [(0.0, 0.0), (wake[0], 0.0), (wake[1], 1.0)])
    return plane, e


def dust(count=36, seed=7, box=((-7, 9), (-6, 14), (-4, 4)), rgb=(0.3, 0.5, 1.0), strength=3.0, born=None):
    """Tiny motes of light drifting through the space (depth of field makes the near ones soft); `born` (from, to)
    seconds: they appear between those times, not before (unlit dust shows as black dots)."""
    rnd = random.Random(seed)
    m, e = emissive("dust", rgb, strength)
    for _ in range(count):
        x, y, z = (rnd.uniform(*box[0]), rnd.uniform(*box[1]), rnd.uniform(*box[2]))
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=1, radius=rnd.uniform(0.008, 0.02), location=(x, y, z))
        d = bpy.context.object
        d.data.materials.append(m)
        keys(d, "location", [(0.0, (x, y, z)), (DUR, (x + rnd.uniform(-0.4, 0.4), y, z + rnd.uniform(0.1, 0.5)))],
             "LINEAR")
        if born:
            s0 = born[0] + rnd.uniform(0, 0.8)
            keys(d, "scale", [(0.0, (0, 0, 0)), (s0, (0, 0, 0)), (s0 + rnd.uniform(0.4, 1.2), (1, 1, 1))])
    return m, e


def _principled(name, rgb):
    m, n, _ = mat(name)
    b = n["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*rgb, 1)
    return m, n, b


def _set(b, value, *names):
    for key in names:  # input names moved between Blender versions; the first that exists wins
        if key in b.inputs:
            b.inputs[key].default_value = value
            return


def metal(name, rgb=(0.9, 0.62, 0.32), roughness=0.28, brushed=0.0):
    """Polished or brushed metal (gold by default; chrome (0.92, 0.93, 0.95), copper (0.95, 0.55, 0.4)). `brushed`
    0..1 stretches the highlights along the surface. Metal shows what it reflects: give it lights or a bright world."""
    m, _, b = _principled(name, rgb)
    _set(b, 1.0, "Metallic")
    _set(b, roughness, "Roughness")
    _set(b, brushed, "Anisotropic")
    return m


def clay(name, rgb, roughness=0.62, soft=0.12):
    """Soft matte clay or toy plastic: light sinks in a little at the edges (subsurface `soft`), no hard shine."""
    m, _, b = _principled(name, rgb)
    _set(b, roughness, "Roughness")
    _set(b, soft, "Subsurface Weight", "Subsurface")
    _set(b, 0.08, "Subsurface Scale")
    _set(b, 0.35, "Sheen Weight", "Sheen")
    return m


def frosted(name, rgb=(0.75, 0.88, 1.0), roughness=0.38, ior=1.45):
    """Frosted glass: light passes through, blurred, so things behind it go soft."""
    m = glass(name, rgb, roughness, ior)
    return m


def iridescent(name, rgb=(0.32, 0.3, 0.36), thickness=480.0, roughness=0.1):
    """A dark base with a thin-film sheen whose colour shifts with the angle (soap bubble, beetle shell, oil)."""
    m, _, b = _principled(name, rgb)
    _set(b, roughness, "Roughness")
    _set(b, 1.0, "Metallic")
    _set(b, thickness, "Thin Film Thickness")
    _set(b, 1.6, "Thin Film IOR")
    return m


def toon(name, rgb, shade=None, light=None, rim=None, split=0.42, rim_width=0.7, sun=(-0.55, -0.5, 0.65)):
    """Cel shading for a drawn look in 3D: flat bands instead of soft shading, the same whatever the scene's lights.
    The lit face `light`, the body `rgb`, the side away `shade` (defaults: the colour lifted and deepened), and a
    crisp rim on the silhouette. `sun`: the direction the drawn light comes from (the camera looks along +Y, so
    -Y is toward it); `split`: where light turns to shade (0..1). Outline the object with `outline(obj)`."""
    light = light or tuple(min(1.0, c * 1.3 + 0.06) for c in rgb)
    shade = shade or tuple(c * 0.5 for c in rgb)
    rim = rim or tuple(min(1.0, c * 1.6 + 0.2) for c in rgb)
    m, n, l = mat(name)
    n.clear()
    geo = n.new("ShaderNodeNewGeometry")
    dot = n.new("ShaderNodeVectorMath")
    dot.operation = "DOT_PRODUCT"
    norm = math.sqrt(sum(c * c for c in sun)) or 1.0
    dot.inputs[1].default_value = tuple(c / norm for c in sun)
    lit = n.new("ShaderNodeMapRange")  # -1..1 -> 0..1
    lit.inputs["From Min"].default_value = -1.0
    bands = n.new("ShaderNodeValToRGB")
    bands.color_ramp.interpolation = "CONSTANT"
    els = bands.color_ramp.elements
    els[0].position, els[0].color = 0.0, (*shade, 1)
    els[1].position, els[1].color = split, (*rgb, 1)
    third = els.new(min(0.95, split + 0.4))
    third.color = (*light, 1)
    fres = n.new("ShaderNodeLayerWeight")
    fres.inputs["Blend"].default_value = rim_width
    edge = n.new("ShaderNodeValToRGB")
    edge.color_ramp.interpolation = "CONSTANT"
    edge.color_ramp.elements[0].color = (0, 0, 0, 1)
    edge.color_ramp.elements[1].position = 0.62
    edge.color_ramp.elements[1].color = (1, 1, 1, 1)
    mix = n.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    a_in, b_in = [i for i in mix.inputs if i.type == "RGBA"]  # the colour sockets (it also has float/vector A, B)
    result = next(o for o in mix.outputs if o.type == "RGBA")
    b_in.default_value = (*rim, 1)
    em = n.new("ShaderNodeEmission")
    out = n.new("ShaderNodeOutputMaterial")
    l.new(geo.outputs["Normal"], dot.inputs[0])
    l.new(dot.outputs["Value"], lit.inputs["Value"])
    l.new(lit.outputs["Result"], bands.inputs["Fac"])
    l.new(fres.outputs["Facing"], edge.inputs["Fac"])
    l.new(edge.outputs["Color"], mix.inputs["Factor"])
    l.new(bands.outputs["Color"], a_in)
    l.new(result, em.inputs["Color"])
    l.new(em.outputs[0], out.inputs[0])
    return m


def outline(o, width=0.025, rgb=(0.03, 0.03, 0.05)):
    """An ink line around an object's silhouette (the inverted-hull trick: a slightly bigger copy, back faces only)."""
    m, n, l = mat(f"{o.name}-ink")
    n.clear()
    e = n.new("ShaderNodeEmission")
    e.inputs[0].default_value = (*rgb, 1)
    out = n.new("ShaderNodeOutputMaterial")
    l.new(e.outputs[0], out.inputs[0])
    m.use_backface_culling = True
    o.data.materials.append(m)
    shell = o.modifiers.new("ink", "SOLIDIFY")
    shell.thickness = -width
    shell.use_flip_normals = True
    shell.material_offset = len(o.data.materials) - 1
    return shell


def smooth(o):
    """Smooth shading on every face (the bevel's harden-normals keeps flat faces flat)."""
    for p in o.data.polygons:
        p.use_smooth = True


def bevel(o, width=0.04, segments=4):
    """Rounded edges, the way real made things are: nothing has a razor edge."""
    mod = o.modifiers.new("bevel", "BEVEL")
    mod.width = width
    mod.segments = segments
    mod.limit_method = "ANGLE"
    try:
        mod.harden_normals = True
    except AttributeError:
        pass
    smooth(o)
    return mod


def rounded_box(name, size=(1, 1, 1), loc=(0, 0, 0), radius=0.08, material=None):
    """A box of that size with rounded edges (radius in scene units, whatever the size)."""
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc)
    o = bpy.context.object
    o.name = name
    for v in o.data.vertices:
        v.co = (v.co.x * size[0], v.co.y * size[1], v.co.z * size[2])
    bevel(o, min(radius, min(size) / 2 * 0.98), 5)
    if material:
        o.data.materials.append(material)
    return o


def cyc(rgb=(0.85, 0.82, 0.78), y=3.0, z=-2.2, width=60.0, depth=30.0, height=30.0, radius=6.0, roughness=0.75):
    """A studio cyclorama: a floor that curves up into a wall with no seam, so things sit in an endless space and
    cast soft shadows. The floor is at height `z`, the curve starts `y` behind the person's card."""
    import bmesh

    prof = [(y - depth, z), (y, z)]
    for i in range(1, 13):
        a = (i / 12) * (math.pi / 2)
        prof.append((y + math.sin(a) * radius, z + (1 - math.cos(a)) * radius))
    prof.append((y + radius, z + height))
    me = bpy.data.meshes.new("cyc")
    bm = bmesh.new()
    left = [bm.verts.new((-width / 2, py, pz)) for py, pz in prof]
    right = [bm.verts.new((width / 2, py, pz)) for py, pz in prof]
    for i in range(len(prof) - 1):
        bm.faces.new((left[i], right[i], right[i + 1], left[i + 1]))
    bm.to_mesh(me)
    bm.free()
    o = obj("cyc", me)
    smooth(o)
    m, _, b = _principled("cyc", rgb)
    _set(b, roughness, "Roughness")
    o.data.materials.append(m)
    return o


def terrain(size=40.0, height=2.2, seed=3, loc=(0, 14, -3.0), low=(0.03, 0.12, 0.05), mid=(0.16, 0.36, 0.1),
            high=(0.5, 0.45, 0.36), cuts=96, flat=True):
    """Rolling land (hills, dunes, a far ridge): a grid lifted by noise, coloured by height, `low` in the valleys,
    `mid` on the slopes, `high` on the tops (grass to rock by default; sand (0.5, 0.36, 0.2) .. for dunes).
    `flat` gives the faceted low-poly look; False is smooth."""
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=cuts, y_subdivisions=cuts, size=size, location=loc)
    o = bpy.context.object
    o.name = "terrain"
    tex = bpy.data.textures.new(f"terrain-{seed}", "CLOUDS")
    tex.noise_scale = size / 6
    tex.noise_depth = 3
    tex.noise_basis = "ORIGINAL_PERLIN"
    rnd = random.Random(seed)
    disp = o.modifiers.new("lift", "DISPLACE")
    disp.texture = tex
    disp.strength = height
    off = obj("terrain-seed", None, (rnd.uniform(-500, 500), rnd.uniform(-500, 500), 0))
    disp.texture_coords = "OBJECT"
    disp.texture_coords_object = off
    if not flat:
        smooth(o)
    m, n, l = mat("terrain")
    b = n["Principled BSDF"]
    _set(b, 0.85, "Roughness")
    geo = n.new("ShaderNodeNewGeometry")
    sep = n.new("ShaderNodeSeparateXYZ")
    rng = n.new("ShaderNodeMapRange")
    rng.inputs["From Min"].default_value = loc[2] - height * 0.35  # the noise lifts around its middle, both ways
    rng.inputs["From Max"].default_value = loc[2] + height * 0.35
    ramp = n.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = (*low, 1)
    ramp.color_ramp.elements[1].color = (*high, 1)
    ramp.color_ramp.elements.new(0.45).color = (*mid, 1)
    l.new(geo.outputs["Position"], sep.inputs[0])
    l.new(sep.outputs["Z"], rng.inputs["Value"])
    l.new(rng.outputs["Result"], ramp.inputs["Fac"])
    l.new(ramp.outputs["Color"], b.inputs["Base Color"])
    o.data.materials.append(m)
    return o


def ocean(size=40.0, loc=(0, 10, -2.4), rgb=(0.02, 0.12, 0.22), waves=1.0, chop=1.2, speed=1.0, seed=1):
    """Moving sea: Blender's ocean simulation (real swell and chop, animated over the section), a deep glossy
    surface that reflects the sky and lights. `waves` scales the swell, `speed` how fast it rolls."""
    bpy.ops.mesh.primitive_plane_add(size=1, location=loc)
    o = bpy.context.object
    o.name = "ocean"
    sim = o.modifiers.new("sea", "OCEAN")
    sim.geometry_mode = "GENERATE"
    sim.spatial_size = int(size)
    sim.size = 1.0
    sim.repeat_x = sim.repeat_y = 1
    sim.resolution = 12
    sim.wave_scale = waves
    sim.choppiness = chop
    sim.random_seed = seed
    keys(sim, "time", [(0.0, 1.0), (DUR, 1.0 + DUR * speed)], "LINEAR")
    smooth(o)
    m, _, b = _principled("ocean", rgb)
    _set(b, 0.06, "Roughness")
    _set(b, 1.33, "IOR")
    _set(b, 0.5, "Coat Weight", "Clearcoat")
    o.data.materials.append(m)
    return o


def sky(top=(0.02, 0.05, 0.16), horizon=(0.55, 0.32, 0.36), ground=None, strength=1.0, height=0.16,
        changes=None):
    """The world as a sky: `horizon` colour at eye level fading up to `top` (`height`: how high the horizon glow
    reaches, 0..1), `ground` below (default: the horizon dimmed, so no dark band shows past the land's edge). It
    lights the scene too. `changes` [(seconds, top, horizon)] moves it over the
    section (night to dawn)."""
    ground = ground or tuple(c * 0.45 for c in horizon)
    w = bpy.context.scene.world
    n, l = w.node_tree.nodes, w.node_tree.links
    n.clear()
    coord = n.new("ShaderNodeTexCoord")
    sep = n.new("ShaderNodeSeparateXYZ")
    up = n.new("ShaderNodeMapRange")
    up.inputs["From Min"].default_value, up.inputs["From Max"].default_value = 0.0, max(0.01, height)
    ramp = n.new("ShaderNodeMix")
    ramp.data_type = "RGBA"
    a_in, b_in = [i for i in ramp.inputs if i.type == "RGBA"]
    a_in.default_value, b_in.default_value = (*horizon, 1), (*top, 1)
    below = n.new("ShaderNodeMix")
    below.data_type = "RGBA"
    c_in, d_in = [i for i in below.inputs if i.type == "RGBA"]
    d_in.default_value = (*ground, 1)
    under = n.new("ShaderNodeMath")
    under.operation = "LESS_THAN"
    under.inputs[1].default_value = -0.02
    bg = n.new("ShaderNodeBackground")
    bg.inputs["Strength"].default_value = strength
    out = n.new("ShaderNodeOutputWorld")
    l.new(coord.outputs["Generated"], sep.inputs[0])
    l.new(sep.outputs["Z"], up.inputs["Value"])
    l.new(up.outputs["Result"], ramp.inputs["Factor"])
    l.new(next(o for o in ramp.outputs if o.type == "RGBA"), c_in)
    l.new(sep.outputs["Z"], under.inputs[0])
    l.new(under.outputs[0], below.inputs["Factor"])
    l.new(next(o for o in below.outputs if o.type == "RGBA"), bg.inputs["Color"])
    l.new(bg.outputs[0], out.inputs[0])
    for t, tp, hz in changes or ():
        b_in.default_value, a_in.default_value = (*tp, 1), (*hz, 1)
        b_in.keyframe_insert("default_value", frame=f(t))
        a_in.keyframe_insert("default_value", frame=f(t))
    return bg


def aim_at(o, target):
    """Turn a light or object so its -Z looks at the target point."""
    import mathutils

    d = mathutils.Vector(target) - mathutils.Vector(o.location)
    o.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    return o


def studio(target=(0.0, 2.0, 0.0), key=(1.0, 0.95, 0.88), fill=(0.75, 0.85, 1.0), rim=(1.0, 0.8, 0.6), power=1.0):
    """A three-light studio on the subject at `target`: a big soft key from the front side, a dim cool fill from the
    other side, a hard rim from behind that draws its silhouette. `power` scales all three."""
    tx, ty, tz = target
    out = []
    for loc, rgb, watts, size in (((tx - 5.5, ty - 5.0, tz + 4.0), key, 420, 4.0),
                                  ((tx + 6.0, ty - 4.0, tz + 1.0), fill, 110, 6.0),
                                  ((tx + 2.5, ty + 5.0, tz + 3.5), rim, 520, 1.2)):
        o, data = light("AREA", loc, rgb, watts * power, size)
        aim_at(o, target)
        out.append(o)
    return out



def haze(density=0.01, rgb=(0.75, 0.8, 0.9), anisotropy=0.35, size=(80.0, 80.0, 30.0), centre=(0.0, 15.0, 5.0)):
    """Air with a little haze in it, so light shows as shafts (EEVEE volumetrics): a lighthouse beam, light through a
    window, a spotlight. It fills a box around the scene (`size`, `centre`), not the world: a world volume is endless
    and turns the sky black. Thin is right (0.005-0.02); thick hides the world."""
    sc = bpy.context.scene
    for attr, v in (("volumetric_tile_size", "4"), ("volumetric_samples", 96), ("use_volumetric_shadows", True),
                    ("volumetric_end", max(size) * 1.5)):
        try:
            setattr(sc.eevee, attr, v)
        except (AttributeError, TypeError):
            pass
    bpy.ops.mesh.primitive_cube_add(size=1, location=centre)
    box = bpy.context.object
    box.name = "haze"
    box.scale = size
    m, n, l = mat("haze")
    n.clear()
    vol = n.new("ShaderNodeVolumePrincipled")
    vol.inputs["Density"].default_value = density
    vol.inputs["Color"].default_value = (*rgb, 1)
    vol.inputs["Anisotropy"].default_value = anisotropy
    out = n.new("ShaderNodeOutputMaterial")
    l.new(vol.outputs[0], out.inputs["Volume"])
    box.data.materials.append(m)
    return box


def beam(origin=(0, 0, 0), aim=(0, 10, 0), length=30.0, spread=6.0, rgb=(1.0, 0.86, 0.62), strength=0.9):
    """A beam of light the way the eye sees one at night: a soft cone from `origin` toward `aim`, bright at its source,
    fading along its length and soft at its edges (never a hard laser line), `spread` degrees wide. Returns the cone
    (rotate it, or its parent, to sweep it). Pair it with a real light inside for what it lights."""
    import mathutils

    r = math.tan(math.radians(spread / 2)) * length
    bpy.ops.mesh.primitive_cone_add(vertices=48, radius1=0.02, radius2=r, depth=length, end_fill_type="NOTHING")
    cone = bpy.context.object
    cone.name = "beam"
    for v in cone.data.vertices:  # its tip at the origin, opening along +Z
        v.co.z += length / 2
    smooth(cone)
    m, n, l = mat("beam")
    n.clear()
    geo = n.new("ShaderNodeNewGeometry")
    coord = n.new("ShaderNodeTexCoord")
    sep = n.new("ShaderNodeSeparateXYZ")
    along = n.new("ShaderNodeMapRange")  # bright at the source, gone by the far end
    along.inputs["From Min"].default_value, along.inputs["From Max"].default_value = 0.0, length
    along.inputs["To Min"].default_value, along.inputs["To Max"].default_value = 1.0, 0.0
    fall = n.new("ShaderNodeMath")
    fall.operation = "POWER"
    fall.inputs[1].default_value = 1.6
    edge = n.new("ShaderNodeLayerWeight")  # soft at its sides: seen side-on, the cone's rim fades to nothing
    edge.inputs["Blend"].default_value = 0.75
    soft = n.new("ShaderNodeMath")
    soft.operation = "SUBTRACT"
    soft.inputs[0].default_value = 1.0
    both = n.new("ShaderNodeMath")
    both.operation = "MULTIPLY"
    em = n.new("ShaderNodeEmission")
    em.inputs["Color"].default_value = (*rgb, 1)
    clear = n.new("ShaderNodeBsdfTransparent")
    add = n.new("ShaderNodeAddShader")
    gain = n.new("ShaderNodeMath")
    gain.operation = "MULTIPLY"
    gain.inputs[1].default_value = strength
    out = n.new("ShaderNodeOutputMaterial")
    l.new(coord.outputs["Object"], sep.inputs[0])
    l.new(sep.outputs["Z"], along.inputs["Value"])
    l.new(along.outputs["Result"], fall.inputs[0])
    l.new(edge.outputs["Facing"], soft.inputs[1])
    l.new(fall.outputs[0], both.inputs[0])
    l.new(soft.outputs[0], both.inputs[1])
    l.new(both.outputs[0], gain.inputs[0])
    l.new(gain.outputs[0], em.inputs["Strength"])
    l.new(em.outputs[0], add.inputs[0])
    l.new(clear.outputs[0], add.inputs[1])
    l.new(add.outputs[0], out.inputs[0])
    _ = geo
    try:
        m.surface_render_method = "BLENDED"
    except AttributeError:
        m.blend_method = "BLEND"
    m.use_backface_culling = False
    cone.data.materials.append(m)
    cone.location = origin
    d = mathutils.Vector(aim) - mathutils.Vector(origin)
    cone.rotation_euler = d.to_track_quat("Z", "Y").to_euler()
    return cone


# ---------------------------------------------------------------- ready-made things: Poly Haven (CC0)
PH_API = "https://api.polyhaven.com"
PH_AGENT = "Flicello/1.0 (open-source video studio; github.com/SIDDHANTH-THAKURI/Flicello)"


def _ph_cache(*parts):
    base = os.environ.get("VIDO_MODELS_DIR") or os.path.join(
        os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "VidoSuper", "models")
    path = os.path.join(base, "polyhaven", *parts)
    os.makedirs(path, exist_ok=True)
    return path


def _ph_get(url, to=None):
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": PH_AGENT})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = r.read()
    if to:
        os.makedirs(os.path.dirname(to), exist_ok=True)
        with open(to, "wb") as fh:
            fh.write(data)
    return data


def _ph_id(words, kind):
    """An asset's id: the words as given when they are one, else the library's best match for them."""
    path = os.path.join(_ph_cache(), f"{kind}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            cat = json.load(fh)
    else:
        cat = json.loads(_ph_get(f"{PH_API}/assets?t={kind}"))
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(cat, fh)
    if words in cat:
        return words
    want = [w.rstrip("s") for w in words.lower().replace("_", " ").split() if len(w) > 1]
    best, score = None, 0
    for aid, a in cat.items():
        name = {t.rstrip("s") for t in (a.get("name", "") + " " + aid.replace("_", " ")).lower().split()}
        tags = {t.rstrip("s") for t in " ".join(a.get("tags", [])).lower().split()}
        sc = sum(3 if w in name else 2 if w in tags else 0 for w in want)
        if sc > score:
            best, score = aid, sc
    return best


def model(what, loc=(0.0, 0.0, 0.0), height=None, rot_z=0.0, res="1k"):
    """A ready-made model from Poly Haven's free library (id from `bash assets`, or words: the best match), downloaded
    once, imported and gathered under one empty: stood on `loc` (its base there), turned `rot_z` degrees and scaled
    to `height` scene units (its real size otherwise). Returns the empty (animate it: keys(empty, "location", ...)),
    or None when nothing in the library matches (then build it from the kit's shapes)."""
    aid = _ph_id(what, "models")
    if not aid:
        print(f"[kit] nothing in the library matches {what!r}")
        return None
    files = json.loads(_ph_get(f"{PH_API}/files/{aid}"))
    sizes = files.get("gltf") or {}
    got = sizes.get(res) or sizes.get("1k") or next(iter(sizes.values()))
    main = got["gltf"]
    folder = _ph_cache("models", aid, res)
    path = os.path.join(folder, os.path.basename(main["url"]))
    if not os.path.exists(path):
        _ph_get(main["url"], path)
        for rel, inc in (main.get("include") or {}).items():
            _ph_get(inc["url"], os.path.join(folder, rel))
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=path)
    new = [o for o in bpy.data.objects if o not in before]
    holder = obj(f"model-{aid}", None, (0, 0, 0))
    for o in new:
        if o.parent is None:
            o.parent = holder
    bpy.context.view_layer.update()
    import mathutils

    pts = [o.matrix_world @ mathutils.Vector(c) for o in new if o.type == "MESH" for c in o.bound_box]
    if pts:
        lo = mathutils.Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
        hi = mathutils.Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
        k = (height / max(1e-6, hi.z - lo.z)) if height else 1.0
        centre = (lo + hi) / 2
        for o in new:  # its base centre to the holder's origin, so loc is where it stands
            if o.parent is holder:
                o.location -= mathutils.Vector((centre.x, centre.y, lo.z))
        holder.scale = (k, k, k)
    holder.location = loc
    holder.rotation_euler = (0, 0, math.radians(rot_z))
    print(f"[kit] model {aid} ({len(new)} parts)")
    return holder


def hdri(what="sunset", strength=0.4, rotation=0.0, res="2k", visible=True):
    """A real sky or place from Poly Haven lighting the whole scene (reflections, soft light from every side; the
    kit's Standard view clips bright skies, so `strength` starts low: raise it for a dim one);
    `visible` False keeps it lighting but not seen behind (the world's own colour shows instead)."""
    aid = _ph_id(what, "hdris")
    if not aid:
        print(f"[kit] no sky matches {what!r}")
        return None
    files = json.loads(_ph_get(f"{PH_API}/files/{aid}"))["hdri"]
    got = (files.get(res) or files.get("1k") or next(iter(files.values())))["hdr"]
    path = os.path.join(_ph_cache("hdris"), f"{aid}_{res}.hdr")
    if not os.path.exists(path):
        _ph_get(got["url"], path)
    w = bpy.context.scene.world
    n, l = w.node_tree.nodes, w.node_tree.links
    n.clear()
    coord = n.new("ShaderNodeTexCoord")
    turn = n.new("ShaderNodeMapping")
    turn.inputs["Rotation"].default_value = (0, 0, math.radians(rotation))
    env = n.new("ShaderNodeTexEnvironment")
    env.image = bpy.data.images.load(path)
    bg = n.new("ShaderNodeBackground")
    bg.inputs["Strength"].default_value = strength
    out = n.new("ShaderNodeOutputWorld")
    l.new(coord.outputs["Generated"], turn.inputs[0])
    l.new(turn.outputs[0], env.inputs[0])
    l.new(env.outputs["Color"], bg.inputs["Color"])
    if visible:
        l.new(bg.outputs[0], out.inputs[0])
    else:  # lights the scene, but the camera sees the world's plain colour
        lp = n.new("ShaderNodeLightPath")
        plain = n.new("ShaderNodeBackground")
        plain.inputs["Color"].default_value = (0, 0, 0, 1)
        mix = n.new("ShaderNodeMixShader")
        l.new(lp.outputs["Is Camera Ray"], mix.inputs[0])
        l.new(bg.outputs[0], mix.inputs[1])
        l.new(plain.outputs[0], mix.inputs[2])
        l.new(mix.outputs[0], out.inputs[0])
    print(f"[kit] sky {aid}")
    return bg



CG_API = "https://ambientcg.com/api/v2/full_json"


def texture(what, scale=1.0, res="1K", tint=None, name=None):
    """A real surface from ambientCG's free library (CC0): stone, brick, wood, metal, rust, sand, fabric... by its id
    (from `bash assets --material ...`) or words (the best match). Downloaded once; colour, roughness, bumps, metal
    and depth all mapped, projected from the object's own axes so it works on any shape (no UVs needed). `scale`:
    how many times it repeats per scene unit. `tint`: (r, g, b) to multiply the colour by. Returns a material to
    append to a mesh, or None when nothing matches (use the kit's own materials then)."""
    import glob
    import urllib.parse
    import zipfile

    aid = what
    if not re.fullmatch(r"[A-Za-z]+\d+[A-Z]?", what or ""):
        words = [w for w in re.findall(r"[a-z]+", (what or "").lower()) if len(w) > 2]
        best, score = None, 0
        found = {}
        for w in words:
            data = json.loads(_ph_get(f"{CG_API}?" + urllib.parse.urlencode({"type": "Material", "q": w, "limit": 30})))
            for a in data.get("foundAssets", []):
                found[a["assetId"]] = found.get(a["assetId"], 0) + 1
        for k_, v in found.items():
            if v > score:
                best, score = k_, v
        aid = best
    if not aid:
        print(f"[kit] no surface matches {what!r}")
        return None
    folder = _ph_cache("materials", f"{aid}_{res}")
    if not glob.glob(os.path.join(folder, "*Color*")):
        zpath = os.path.join(folder, f"{aid}_{res}-JPG.zip")
        _ph_get(f"https://ambientcg.com/get?file={aid}_{res}-JPG.zip", zpath)
        with zipfile.ZipFile(zpath) as z:
            z.extractall(folder)
        os.remove(zpath)

    def part(kind):
        hits = sorted(glob.glob(os.path.join(folder, f"*_{kind}.jpg")) + glob.glob(os.path.join(folder, f"*_{kind}.png")))
        return hits[0] if hits else None

    m, n, l = mat(name or f"surface-{aid}")
    b = n["Principled BSDF"]
    coord = n.new("ShaderNodeTexCoord")
    mapping = n.new("ShaderNodeMapping")
    mapping.inputs["Scale"].default_value = (scale, scale, scale)
    l.new(coord.outputs["Object"], mapping.inputs[0])

    def image(path, colour):
        t = n.new("ShaderNodeTexImage")
        t.image = bpy.data.images.load(path)
        t.image.colorspace_settings.name = "sRGB" if colour else "Non-Color"
        t.projection = "BOX"
        t.projection_blend = 0.25
        l.new(mapping.outputs[0], t.inputs[0])
        return t

    if (p := part("Color")):
        c = image(p, True)
        if tint:
            mix = n.new("ShaderNodeMix")
            mix.data_type = "RGBA"
            mix.blend_type = "MULTIPLY"
            mix.inputs["Factor"].default_value = 1.0
            a_in, b_in = [i for i in mix.inputs if i.type == "RGBA"]
            b_in.default_value = (*tint, 1)
            l.new(c.outputs["Color"], a_in)
            l.new(next(o for o in mix.outputs if o.type == "RGBA"), b.inputs["Base Color"])
        else:
            l.new(c.outputs["Color"], b.inputs["Base Color"])
    if (p := part("Roughness")):
        l.new(image(p, False).outputs["Color"], b.inputs["Roughness"])
    if (p := part("Metalness")):
        l.new(image(p, False).outputs["Color"], b.inputs["Metallic"])
    if (p := part("NormalGL")):
        nm = n.new("ShaderNodeNormalMap")
        l.new(image(p, False).outputs["Color"], nm.inputs["Color"])
        l.new(nm.outputs["Normal"], b.inputs["Normal"])
    if (p := part("Displacement")):
        disp = n.new("ShaderNodeDisplacement")
        disp.inputs["Scale"].default_value = 0.03 / max(scale, 1e-3)
        l.new(image(p, False).outputs["Color"], disp.inputs["Height"])
        l.new(disp.outputs[0], n["Material Output"].inputs["Displacement"])
        try:
            m.displacement_method = "BOTH"
        except (AttributeError, TypeError):
            pass
    print(f"[kit] surface {aid}")
    return m


def end():
    """Render what Flicello asked for: stills at the asked seconds (look/<t>.png), the person alone there on a clear
    background (alone: Flicello measures their framing with it), or every frame (frames/f_####.png)."""
    sc = bpy.context.scene
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_mode = "RGB"
    if MODE == "alone":
        for o in bpy.data.objects:
            if o.type != "CAMERA" and not o.name.startswith("person"):
                o.hide_render = True
        sc.render.film_transparent = True
        sc.render.image_settings.color_mode = "RGBA"
    if MODE in ("stills", "alone"):
        out = os.path.join(DESK, ".look3d" if MODE == "stills" else ".alone3d")
        os.makedirs(out, exist_ok=True)
        for t in [float(x) for x in ASKED.split(",") if x.strip()]:
            sc.frame_set(f(min(max(0.0, t), DUR - 1.0 / FPS)))
            sc.render.filepath = os.path.join(out, f"t{t:07.3f}.png")
            bpy.ops.render.render(write_still=True)
    else:
        out = ASKED or os.path.join(DESK, "frames")
        os.makedirs(out, exist_ok=True)
        sc.render.filepath = os.path.join(out, "f_")
        bpy.ops.render.render(animation=True)
