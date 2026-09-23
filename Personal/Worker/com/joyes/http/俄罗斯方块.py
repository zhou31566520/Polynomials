import pygame
import random
import os

pygame.init()

BLOCK_SIZE = 15
GRID_WIDTH = 20
GRID_HEIGHT = 50
SCREEN_WIDTH = BLOCK_SIZE * GRID_WIDTH + 200
SCREEN_HEIGHT = BLOCK_SIZE * GRID_HEIGHT

# 颜色
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
GRAY = (40, 40, 40)
COLORS = [
    # (0, 255, 255),
    # (255, 255, 0),
    # (128, 0, 128),
    # (0, 0, 255),
    # (255, 165, 0),
    # (0, 255, 0),
    (224, 209, 214)
]

SHAPES = [
    [[1,1,1,1,1]],
    [[1,1],[1,1]],
    [[0,1,0],[1,1,1]],
    [[1,0,0],[1,1,1]],
    [[0,0,1],[1,1,1]],
    [[1,1,0],[0,1,1]],
    [[0,1,1],[1,1,0]]
]

class Piece:
    def __init__(self):
        self.shape = random.choice(SHAPES)
        self.color = random.choice(COLORS)
        self.x = GRID_WIDTH // 2 - len(self.shape[0])//2
        self.y = 0

    def rotate(self):
        return [list(row) for row in zip(*self.shape[::-1])]

class Game:
    def __init__(self):
        self.screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
        pygame.display.set_caption("俄罗斯方块")
        self.clock = pygame.time.Clock()
        self.grid = [[BLACK for _ in range(GRID_WIDTH)] for _ in range(GRID_HEIGHT)]
        self.current_piece = Piece()
        self.next_piece = Piece()
        self.score = 8000
        self.game_over = False
        self.paused = False

        self.speed_rate = 0.8  # 下落速度增加率
        self.fall_level = 1
        self.fall_speed = 500  # 默认自动下落间隔ms
        self.fast_fall_interval = 80   # 按住↓时快速下落间隔

        # 长按左右移动控制参数
        self.move_delay = 160   # 按下后多久开始连续移动（初始延迟）
        self.move_interval = 55 # 长按连续移动的间隔
        self.last_move_time = 0
        self.move_start_time = 0  # 记录按键按下的时间（用于区分单击/长按）
        self.move_dir = 0         # 当前按住的方向：-1=左，1=右，0=无

        # 字体：优先系统中文字体，跨平台兜底
        self.font_cn = self._load_cn_font(36)
        self.font_big = self._load_cn_font(72)

    def _load_cn_font(self, size):
        """加载支持中文的字体，规避 pygame.font.match_font 在 Windows 上的 bug"""
        # 1. 优先直接加载常见字体文件路径（最可靠）
        font_paths = [
            r"C:\Windows\Fonts\msyh.ttc",
            r"C:\Windows\Fonts\msyh.ttf",
            r"C:\Windows\Fonts\simhei.ttf",
            r"C:\Windows\Fonts\simfang.ttf",
            r"C:\Windows\Fonts\simsun.ttc",
            "/System/Library/Fonts/PingFang.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        ]
        for fp in font_paths:
            if os.path.isfile(fp):
                return pygame.font.Font(fp, size)

        # 2. 手动遍历 get_fonts()（不调 match_font，避免 Windows bug）
        cn_keywords = ["yahei", "hei", "fang", "song", "pingfang", "cjk", "microhei", "arialuni"]
        try:
            for fname in pygame.font.get_fonts():
                if any(kw in fname.lower() for kw in cn_keywords):
                    try:
                        return pygame.font.SysFont(fname, size)
                    except Exception:
                        continue
        except Exception:
            pass

        # 3. 兜底：默认字体
        return pygame.font.Font(None, size)

    def draw_grid(self):
        for y in range(GRID_HEIGHT):
            for x in range(GRID_WIDTH):
                pygame.draw.rect(
                    self.screen,
                    self.grid[y][x],
                    (x*BLOCK_SIZE, y*BLOCK_SIZE, BLOCK_SIZE-1, BLOCK_SIZE-1)
                )

    def draw_piece(self, piece):
        for y, row in enumerate(piece.shape):
            for x, cell in enumerate(row):
                if cell:
                    px = piece.x + x
                    py = piece.y + y
                    pygame.draw.rect(
                        self.screen,
                        piece.color,
                        (px*BLOCK_SIZE, py*BLOCK_SIZE, BLOCK_SIZE-1, BLOCK_SIZE-1)
                    )

    def draw_next_piece(self):
        """在右侧绘制下一个方块预览"""
        preview_x = GRID_WIDTH * BLOCK_SIZE + 20
        preview_y = 100
        preview_size = 140

        # 预览框背景和边框
        pygame.draw.rect(self.screen, (50,50,50), (preview_x, preview_y, preview_size, preview_size))
        pygame.draw.rect(self.screen, (120,120,120), (preview_x, preview_y, preview_size, preview_size), 2)

        title = self.font_cn.render("Next", True, WHITE)
        self.screen.blit(title, (preview_x, preview_y - 28))

        # 计算方块形状尺寸，居中绘制
        shape = self.next_piece.shape
        rows = len(shape)
        cols = len(shape[0])
        cell = 22  # 预览用小格子
        offset_x = preview_x + (preview_size - cols * cell) // 2
        offset_y = preview_y + (preview_size - rows * cell) // 2

        for y, row in enumerate(shape):
            for x, cell_val in enumerate(row):
                if cell_val:
                    rect = (offset_x + x*cell, offset_y + y*cell, cell-1, cell-1)
                    pygame.draw.rect(self.screen, self.next_piece.color, rect)

    def draw_ui(self):
        score_text = self.font_cn.render(f"分数: {self.score}", True, WHITE)
        self.screen.blit(score_text, (GRID_WIDTH*BLOCK_SIZE + 20, 50))

        # 下一个方块预览
        self.draw_next_piece()

        # 显示操作提示
        tip_y = 270  # 预览框(100+140)上方
        tip1 = self.font_cn.render("↑ 旋转", True, (180,180,180))
        tip2 = self.font_cn.render("← → 移动", True, (180,180,180))
        tip3 = self.font_cn.render("↓ 加速下落", True, (180,180,180))
        tip4 = self.font_cn.render("空格 暂停", True, (180,180,180))
        self.screen.blit(tip1, (GRID_WIDTH*BLOCK_SIZE + 20, tip_y))
        self.screen.blit(tip2, (GRID_WIDTH*BLOCK_SIZE + 20, tip_y + 36))
        self.screen.blit(tip3, (GRID_WIDTH*BLOCK_SIZE + 20, tip_y + 72))
        self.screen.blit(tip4, (GRID_WIDTH*BLOCK_SIZE + 20, tip_y + 108))

        # 暂停遮罩
        if self.paused:
            pause_text = self.font_big.render("PAUSED", True, (255,215,0))
            hint = self.font_cn.render("按空格继续", True, WHITE)
            self.screen.blit(pause_text, (GRID_WIDTH*BLOCK_SIZE//2 - 90, SCREEN_HEIGHT//2 - 40))
            self.screen.blit(hint, (GRID_WIDTH*BLOCK_SIZE//2 - 70, SCREEN_HEIGHT//2 + 20))

    def check_collision(self, shape, x, y):
        for dy, row in enumerate(shape):
            for dx, cell in enumerate(row):
                if cell:
                    nx = x + dx
                    ny = y + dy
                    if nx <0 or nx >= GRID_WIDTH or ny >= GRID_HEIGHT:
                        return True
                    if ny >=0 and self.grid[ny][nx] != BLACK:
                        return True
        return False

    def lock_piece(self):
        for y, row in enumerate(self.current_piece.shape):
            for x, cell in enumerate(row):
                if cell:
                    self.grid[self.current_piece.y + y][self.current_piece.x + x] = self.current_piece.color
        self.clear_lines()
        self.current_piece = self.next_piece
        self.next_piece = Piece()
        if self.check_collision(self.current_piece.shape, self.current_piece.x, self.current_piece.y):
            self.game_over = True

    def clear_lines(self):
        lines = 0
        y = GRID_HEIGHT - 1
        while y >= 0:
            if all(cell != BLACK for cell in self.grid[y]):
                lines += 1
                # 把上方所有行下移一行
                for yy in range(y, 0, -1):
                    self.grid[yy] = self.grid[yy - 1][:]
                self.grid[0] = [BLACK] * GRID_WIDTH
                # 消行后 y 不变（因为新下移到 y 位置的行可能也是满的）
            else:
                y -= 1
        self.score += lines * 100
        if self.score/1000 > self.fall_level and self.fall_level < 8:
            self.fall_level += 1
            self.fall_speed = int(self.speed_rate * self.fall_speed)
            # 同步增加左右移动速度
            self.move_delay = int(self.speed_rate * self.move_delay)  # 按下后多久开始连续移动（初始延迟）
            self.move_interval = int(self.speed_rate * self.move_interval)  # 长按连续移动的间隔

    def run(self):
        last_fall = pygame.time.get_ticks()
        while not self.game_over:
            now = pygame.time.get_ticks()

            # 事件循环：处理关闭、旋转、空格暂停、左右键按下/松开
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    return
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_SPACE:
                        self.paused = not self.paused
                        last_fall = now  # 暂停恢复后重置下落计时
                    elif event.key == pygame.K_UP and not self.paused:
                        rotated = self.current_piece.rotate()
                        if not self.check_collision(rotated, self.current_piece.x, self.current_piece.y):
                            self.current_piece.shape = rotated
                    elif event.key in (pygame.K_LEFT, pygame.K_RIGHT):
                        # 按下瞬间立即移动一格（实现单击有效）
                        if event.key == pygame.K_LEFT:
                            if not self.check_collision(self.current_piece.shape, self.current_piece.x-1, self.current_piece.y):
                                self.current_piece.x -= 1
                            self.move_dir = -1
                        else:
                            if not self.check_collision(self.current_piece.shape, self.current_piece.x+1, self.current_piece.y):
                                self.current_piece.x += 1
                            self.move_dir = 1
                        self.move_start_time = now
                        self.last_move_time = now
                if event.type == pygame.KEYUP:
                    if event.key in (pygame.K_LEFT, pygame.K_RIGHT):
                        # 松开对应方向键则停止长按移动
                        if (event.key == pygame.K_LEFT and self.move_dir == -1) or \
                           (event.key == pygame.K_RIGHT and self.move_dir == 1):
                            self.move_dir = 0

            # 暂停时跳过游戏逻辑更新
            if self.paused:
                self.screen.fill(GRAY)
                self.draw_grid()
                self.draw_piece(self.current_piece)
                self.draw_ui()
                pygame.display.update()
                self.clock.tick(60)
                continue

            keys = pygame.key.get_pressed()

            # 长按连续移动：仅在超过初始延迟后才自动重复
            if self.move_dir != 0:
                held_time = now - self.move_start_time
                if held_time > self.move_delay and now - self.last_move_time > self.move_interval:
                    target_x = self.current_piece.x + self.move_dir
                    if not self.check_collision(self.current_piece.shape, target_x, self.current_piece.y):
                        self.current_piece.x = target_x
                    self.last_move_time = now

            # 判断当前下落速度
            current_fall_interval = self.fast_fall_interval if keys[pygame.K_DOWN] else self.fall_speed

            # 下落逻辑
            if now - last_fall > current_fall_interval:
                if not self.check_collision(self.current_piece.shape, self.current_piece.x, self.current_piece.y+1):
                    self.current_piece.y +=1
                else:
                    self.lock_piece()
                last_fall = now

            self.screen.fill(GRAY)
            self.draw_grid()
            self.draw_piece(self.current_piece)
            self.draw_ui()
            pygame.display.update()
            self.clock.tick(60)

        # 游戏结束画面
        over_text = self.font_big.render("GAME OVER", True, (255,0,0))
        self.screen.blit(over_text, (20, SCREEN_HEIGHT//2))
        pygame.display.update()
        pygame.time.wait(2000)

if __name__ == "__main__":
    game = Game()
    game.run()