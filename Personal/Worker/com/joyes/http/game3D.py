from direct.showbase.ShowBase import ShowBase
from direct.task import Task
from direct.actor.Actor import Actor
from panda3d.core import (
    CardMaker, GeomNode, Geom, GeomVertexFormat, GeomVertexData,
    GeomVertexWriter, GeomTriangles, Texture, TextureStage,
    AmbientLight, DirectionalLight, Vec3, Vec4, Point3,
    WindowProperties
)
import math
import random


class PlainMap(ShowBase):
    def __init__(self):
        super().__init__()
        self.setBackgroundColor(0.53, 0.81, 0.92)  # 天空蓝

        # ---- 地形参数 ----
        self.terrain_size = 200      # 地形边长
        self.tile_size = 10          # 单块地砖大小
        self.grid_count = self.terrain_size // self.tile_size

        # ---- 摄像机 FOV ----
        self.camLens.setFov(75)

        # ---- 光照 ----
        self._setup_lighting()

        # ---- 生成平原地形 ----
        self._build_terrain()

        # ---- 生成装饰物（树、石头）----
        self._scatter_trees(count=30)
        self._scatter_rocks(count=15)

        # ---- 玩家控制器 ----
        self._setup_player()

        # ---- 输入控制 ----
        self.keys = {}
        self.accept("w", self.keys.__setitem__, ["w", True])
        self.accept("a", self.keys.__setitem__, ["a", True])
        self.accept("s", self.keys.__setitem__, ["s", True])
        self.accept("d", self.keys.__setitem__, ["d", True])
        self.accept("escape", self._release_mouse)
        self.accept("mouse1", self._grab_mouse)

        # 鼠标锁定
        self._grab_mouse()

        # 每帧更新
        self.taskMgr.add(self._update, "update")

    # ---------- 光照 ----------
    def _setup_lighting(self):
        # 环境光
        ambient = AmbientLight("ambient")
        ambient.setColor(Vec4(0.4, 0.4, 0.4, 1))
        self.render.attachNewNode(ambient)

        # 太阳光（方向光）
        sun = DirectionalLight("sun")
        sun.setColor(Vec4(1.0, 0.95, 0.85, 1))
        sun.setShadowCaster(True, 2048, 2048)
        sun.getLens().setFilmSize(200, 200)
        sun.getLens().setNearFar(0.5, 500)
        sun_node = self.render.attachNewNode(sun)
        sun_node.setPos(50, -80, 100)
        sun_node.lookAt(0, 0, 0)
        self.render.attachNewNode(sun_node.node())

    # ---------- 地形 ----------
    def _build_terrain(self):
        """用 CardMaker 生成一块大平原，重复草地纹理"""
        cm = CardMaker("ground")
        cm.setFrame(-self.terrain_size // 2, self.terrain_size // 2,
                    -self.terrain_size // 2, self.terrain_size // 2)
        ground = self.render.attachNewNode(cm.generate())
        ground.setP(-90)  # 从 XY 平面转到 XZ 平面（水平）

        # 设置纹理：用程序生成的简单草地
        grass_tex = self._make_grass_texture()
        ts = TextureStage("ts")
        ground.setTexture(ts, grass_tex)
        # 让纹理重复多次（tiling）
        ground.setTexScale(ts, self.terrain_size // 20, self.terrain_size // 20)
        ground.setColor(0.3, 0.55, 0.25)  # 草地底色

        # 边界围栏（视觉参考）
        self._build_border()

    def _make_grass_texture(self):
        """生成一张简易草地纹理（256x256）"""
        size = 256
        tex = Texture("grass")
        tex.setup2dTexture(size, size, Texture.TUnsignedByte, Texture.FRgba)
        import struct
        data = bytearray()
        for _ in range(size * size):
            g = random.randint(90, 160)
            r = random.randint(40, 90)
            b = random.randint(30, 70)
            data += struct.pack("BBBB", r, g, b, 255)
        tex.setRamImage(bytes(data))
        return tex

    # ---------- 程序化几何生成 ----------
    def _make_cylinder(self, radius=1.0, height=1.0, segments=16):
        """生成圆柱体 GeomNode"""
        vformat = GeomVertexFormat.getV3n3()
        vdata = GeomVertexData("cyl", vformat, Geom.UHStatic)
        vdata.setNumRows(segments * 2 + 2)
        vertex = GeomVertexWriter(vdata, "vertex")
        normal = GeomVertexWriter(vdata, "normal")

        half_h = height / 2.0
        for i in range(segments):
            angle = i * 2 * math.pi / segments
            x = math.cos(angle) * radius
            y = math.sin(angle) * radius
            # 侧面上半
            vertex.addData3f(x, y, half_h)
            normal.addData3f(x / radius, y / radius, 0)
            # 侧面下半
            vertex.addData3f(x, y, -half_h)
            normal.addData3f(x / radius, y / radius, 0)
        # 顶面中心
        vertex.addData3f(0, 0, half_h)
        normal.addData3f(0, 0, 1)
        # 底面中心
        vertex.addData3f(0, 0, -half_h)
        normal.addData3f(0, 0, -1)

        # 构建三角面
        tris = GeomTriangles(Geom.UHStatic)
        for i in range(segments):
            a = i * 2
            b = (i * 2 + 2) % (segments * 2)
            c = i * 2 + 1
            d = (i * 2 + 3) % (segments * 2)
            tris.addVertices(a, b, c)
            tris.addVertices(c, b, d)
        top_c = segments * 2
        bot_c = segments * 2 + 1
        for i in range(segments):
            a = i * 2
            b = (i * 2 + 2) % (segments * 2)
            tris.addVertices(a, top_c, b)      # 顶面
            tris.addVertices(bot_c, a + 1, b + 1)  # 底面

        geom = Geom(vdata)
        geom.addPrimitive(tris)
        node = GeomNode("cylinder")
        node.addGeom(geom)
        return self.render.attachNewNode(node)

    def _make_sphere(self, radius=1.0, segments=16):
        """生成球体 GeomNode（UV 球）"""
        vformat = GeomVertexFormat.getV3n3()
        vdata = GeomVertexData("sphere", vformat, Geom.UHStatic)
        rows = segments // 2
        vdata.setNumRows(rows * segments + 2)
        vertex = GeomVertexWriter(vdata, "vertex")
        normal = GeomVertexWriter(vdata, "normal")

        for r in range(rows):
            phi = (r + 1) * math.pi / rows
            for s in range(segments):
                theta = s * 2 * math.pi / segments
                x = radius * math.sin(phi) * math.cos(theta)
                y = radius * math.sin(phi) * math.sin(theta)
                z = radius * math.cos(phi)
                vertex.addData3f(x, y, z)
                normal.addData3f(x / radius, y / radius, z / radius)
        # 北极
        vertex.addData3f(0, 0, radius)
        normal.addData3f(0, 0, 1)
        # 南极
        vertex.addData3f(0, 0, -radius)
        normal.addData3f(0, 0, -1)

        tris = GeomTriangles(Geom.UHStatic)
        north = rows * segments
        south = rows * segments + 1
        for r in range(rows - 1):
            for s in range(segments):
                a = r * segments + s
                b = r * segments + (s + 1) % segments
                c = (r + 1) * segments + s
                d = (r + 1) * segments + (s + 1) % segments
                tris.addVertices(a, c, b)
                tris.addVertices(b, c, d)
        # 南北极帽
        for s in range(segments):
            a = 0 * segments + s
            b = 0 * segments + (s + 1) % segments
            tris.addVertices(a, north, b)
            a = (rows - 1) * segments + s
            b = (rows - 1) * segments + (s + 1) % segments
            tris.addVertices(south, b, a)

        geom = Geom(vdata)
        geom.addPrimitive(tris)
        node = GeomNode("sphere")
        node.addGeom(geom)
        return self.render.attachNewNode(node)

    def _make_box(self, sx=1, sy=1, sz=1):
        """生成长方体 GeomNode"""
        vformat = GeomVertexFormat.getV3n3()
        vdata = GeomVertexData("box", vformat, Geom.UHStatic)
        vdata.setNumRows(24)
        vertex = GeomVertexWriter(vdata, "vertex")
        normal = GeomVertexWriter(vdata, "normal")
        hx, hy, hz = sx / 2, sy / 2, sz / 2
        # 6 面 x 4 顶点
        faces = [
            # (+x)
            ((hx,hy,hz),(hx,-hy,hz),(hx,-hy,-hz),(hx,hy,-hz), (1,0,0)),
            # (-x)
            ((-hx,-hy,hz),(-hx,hy,hz),(-hx,hy,-hz),(-hx,-hy,-hz), (-1,0,0)),
            # (+y)
            ((hx,hy,-hz),(hx,hy,hz),(-hx,hy,hz),(-hx,hy,-hz), (0,1,0)),
            # (-y)
            ((hx,-hy,hz),(hx,-hy,-hz),(-hx,-hy,-hz),(-hx,-hy,hz), (0,-1,0)),
            # (+z)
            ((-hx,-hy,hz),(-hx,hy,hz),(hx,hy,hz),(hx,-hy,hz), (0,0,1)),
            # (-z)
            ((-hx,hy,-hz),(-hx,-hy,-hz),(hx,-hy,-hz),(hx,hy,-hz), (0,0,-1)),
        ]
        for p1, p2, p3, p4, n in faces:
            for p in (p1, p2, p3, p4):
                vertex.addData3f(*p)
                normal.addData3f(*n)

        tris = GeomTriangles(Geom.UHStatic)
        for i in range(6):
            o = i * 4
            tris.addVertices(o, o + 1, o + 2)
            tris.addVertices(o, o + 2, o + 3)

        geom = Geom(vdata)
        geom.addPrimitive(tris)
        node = GeomNode("box")
        node.addGeom(geom)
        return self.render.attachNewNode(node)

    def _build_border(self):
        """在平原四周放几堵矮墙作边界"""
        half = self.terrain_size // 2
        walls = [
            (0, half, 1, self.terrain_size, 2, 2),
            (0, -half, 1, self.terrain_size, 2, 2),
            (half, 0, 1, 2, 2, self.terrain_size),
            (-half, 0, 1, 2, 2, self.terrain_size),
        ]
        for x, y, z, sx, sy, sz in walls:
            w = self._make_box(sx, sy, sz)
            w.setPos(x, y, z)
            w.setColor(0.55, 0.55, 0.5)

    # ---------- 装饰物 ----------
    def _scatter_trees(self, count=30):
        """随机种树（圆柱树干 + 球形树冠）"""
        half = self.terrain_size // 2 - 5
        for _ in range(count):
            x = random.uniform(-half, half)
            y = random.uniform(-half, half)
            # 树干
            trunk = self._make_cylinder(radius=0.3, height=4)
            trunk.setPos(x, y, 2)
            trunk.setColor(0.45, 0.3, 0.18)
            # 树冠
            leaves = self._make_sphere(radius=1.8)
            leaves.setPos(x, y, 5.5)
            leaves.setColor(0.2, random.uniform(0.45, 0.6), 0.15)

    def _scatter_rocks(self, count=15):
        """随机放石头（压扁球）"""
        half = self.terrain_size // 2 - 5
        for _ in range(count):
            x = random.uniform(-half, half)
            y = random.uniform(-half, half)
            rock = self._make_sphere(radius=1.0)
            rock.setPos(x, y, 0.4)
            s = random.uniform(0.5, 1.5)
            rock.setScale(s * 1.2, s, s * 0.7)
            rock.setColor(random.uniform(0.4, 0.6),
                          random.uniform(0.4, 0.6),
                          random.uniform(0.4, 0.6))

    # ---------- 玩家 & 控制 ----------
    def _setup_player(self):
        """玩家锚点：相机挂在上面，WASD 移动，鼠标转向"""
        self.player = self.render.attachNewNode("player")
        self.player.setPos(0, 0, 1.7)
        self.camera.reparentTo(self.player)
        self.camera.setPos(0, 0, 0)  # 眼高 1.7

        self.move_speed = 30.0      # 单位/秒
        self.mouse_sensitivity = 0.15
        self.yaw = 0.0
        self.pitch = 0.0

    def _grab_mouse(self):
        wp = WindowProperties()
        wp.setCursorHidden(True)
        wp.setMouseMode(WindowProperties.MRelative)
        self.win.requestProperties(wp)

    def _release_mouse(self):
        wp = WindowProperties()
        wp.setCursorHidden(False)
        wp.setMouseMode(WindowProperties.MAbsolute)
        self.win.requestProperties(wp)

    def _update(self, task):
        dt = globalClock.getDt()

        # 鼠标控制视角
        if self.mouseWatcherNode.hasMouse():
            mx = self.mouseWatcherNode.getMouseX()
            my = self.mouseWatcherNode.getMouseY()
            if mx != 0 or my != 0:
                self.yaw -= mx * self.mouse_sensitivity * 60 * dt
                self.pitch += my * self.mouse_sensitivity * 60 * dt
                self.pitch = max(-80, min(80, self.pitch))
                self.player.setH(self.yaw)
                self.camera.setP(self.pitch)
                # 重置鼠标到中心
                self.win.movePointer(0, self.win.getXSize() // 2, self.win.getYSize() // 2)

        # WASD 移动（基于朝向）
        forward = Vec3(math.sin(math.radians(self.yaw)),
                       -math.cos(math.radians(self.yaw)), 0)
        right = Vec3(math.cos(math.radians(self.yaw)),
                     math.sin(math.radians(self.yaw)), 0)
        pos = self.player.getPos()
        if self.keys.get("w"):
            pos += forward * self.move_speed * dt
        if self.keys.get("s"):
            pos -= forward * self.move_speed * dt
        if self.keys.get("a"):
            pos -= right * self.move_speed * dt
        if self.keys.get("d"):
            pos += right * self.move_speed * dt
        # 边界限制
        limit = self.terrain_size // 2 - 2
        pos.x = max(-limit, min(limit, pos.x))
        pos.y = max(-limit, min(limit, pos.y))
        pos.z = 1.7  # 固定眼高
        self.player.setPos(pos)

        return Task.cont


if __name__ == "__main__":
    app = PlainMap()
    app.run()