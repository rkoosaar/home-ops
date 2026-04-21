import random
import sys

import pygame

# --- Constants ---
WINDOW_SIZE = 400
GRID_SIZE = 20
SPEED = 10  # Higher is faster
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
GREEN = (0, 255, 0)
RED = (255, 0, 0)
GRAY = (40, 40, 40)

# --- Initialization ---
pygame.init()

# Screen setup
screen = pygame.display.set_mode((WINDOW_SIZE, WINDOW_SIZE))
pygame.display.set_caption("Classic Snake")
clock = pygame.time.Clock()

# --- Game Variables ---
snake = []
start_pos = (10 * GRID_SIZE, 10 * GRID_SIZE)
snake.append(start_pos)
snake.append((start_pos[0], start_pos[1] + GRID_SIZE))
snake.append((start_pos[0], start_pos[1] + 2 * GRID_SIZE))
snake_length = 3

food_pos = (15 * GRID_SIZE, 15 * GRID_SIZE)
direction = pygame.K_RIGHT
score = 0
game_over = False

# --- Helper Functions ---


def draw_grid():
    """Draws a subtle background grid."""
    for x in range(0, WINDOW_SIZE, GRID_SIZE):
        pygame.draw.line(screen, GRAY, (x, 0), (x, WINDOW_SIZE))
        for y in range(0, WINDOW_SIZE, GRID_SIZE):
            pygame.draw.line(screen, GRAY, (0, y), (WINDOW_SIZE, y))


def draw_snake():
    """Draws the snake body."""
    for idx, segment in enumerate(snake):
        color = GREEN if idx == 0 else (0, 200, 0)
        pygame.draw.rect(screen, color, segment, 0)
        pygame.draw.rect(screen, BLACK, segment, 2)  # Border


def draw_food():
    """Draws the food circle."""
    x, y = food_pos
    rect = pygame.Rect(x, y, GRID_SIZE, GRID_SIZE)
    pygame.draw.rect(screen, RED, rect, 0)
    pygame.draw.rect(screen, BLACK, rect, 2)


def generate_food():
    """Generates a new food position not on the snake."""
    global food_pos
    while True:
        food_pos = (
            random.randint(0, (WINDOW_SIZE // GRID_SIZE) - 1) * GRID_SIZE,
            random.randint(0, (WINDOW_SIZE // GRID_SIZE) - 1) * GRID_SIZE,
        )
        # Check if food spawned on snake body
        on_snake = any(pos == food_pos for pos in snake)
        if not on_snake:
            break


def reset_game():
    """Resets game state to start."""
    global snake, food_pos, direction, score, game_over
    snake = []
    start_pos = (10 * GRID_SIZE, 10 * GRID_SIZE)
    snake.append(start_pos)
    snake.append((start_pos[0], start_pos[1] + GRID_SIZE))
    snake.append((start_pos[0], start_pos[1] + 2 * GRID_SIZE))
    snake_length = 3
    direction = pygame.K_RIGHT
    score = 0
    generate_food()
    game_over = False


def update_game():
    """Updates the snake movement and game logic."""
    global game_over, score

    # Calculate new head position
    head_x, head_y = snake[0]

    if direction == pygame.K_UP:
        new_head = (head_x, head_y - GRID_SIZE)
    elif direction == pygame.K_DOWN:
        new_head = (head_x, head_y + GRID_SIZE)
    elif direction == pygame.K_LEFT:
        new_head = (head_x - GRID_SIZE, head_y)
    elif direction == pygame.K_RIGHT:
        new_head = (head_x + GRID_SIZE, head_y)

    # Check for collision with walls
    if (
        new_head[0] < 0
        or new_head[0] >= WINDOW_SIZE
        or new_head[1] < 0
        or new_head[1] >= WINDOW_SIZE
    ):
        game_over = True
        return

    # Check for collision with self
    if new_head in snake[:-1]:
        game_over = True
        return

    # Move snake
    snake.insert(0, new_head)

    # Check for food
    if new_head == food_pos:
        score += 10
        generate_food()
    else:
        # Remove tail if not eating
        snake.pop()


# --- Main Loop ---
font = pygame.font.Font(None, 36)
large_font = pygame.font.Font(None, 74)

running = True

while running:
    # 1. Event Handling
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False

        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_q:  # Quit shortcut
                running = False

            if game_over:
                if event.key == pygame.K_r:
                    reset_game()
                else:
                    continue  # Ignore other keys until restart

            if not game_over:
                if event.key == pygame.K_UP and direction != pygame.K_DOWN:
                    direction = pygame.K_UP
                elif event.key == pygame.K_DOWN and direction != pygame.K_UP:
                    direction = pygame.K_DOWN
                elif event.key == pygame.K_LEFT and direction != pygame.K_RIGHT:
                    direction = pygame.K_LEFT
                elif event.key == pygame.K_RIGHT and direction != pygame.K_LEFT:
                    direction = pygame.K_RIGHT

    if not game_over:
        update_game()

    # 2. Drawing
    screen.fill(BLACK)
    draw_grid()
    draw_snake()
    draw_food()

    # 3. Score Display
    score_text = font.render(f"Score: {score}", True, WHITE)
    screen.blit(score_text, (10, 10))

    if game_over:
        # Game Over Text
        go_text = large_font.render("GAME OVER", True, RED)
        rect = go_text.get_rect(center=(WINDOW_SIZE // 2, WINDOW_SIZE // 2))
        screen.blit(go_text, rect)
        restart_text = font.render("Press 'R' to Restart or 'Q' to Quit", True, WHITE)
        restart_rect = restart_text.get_rect(
            center=(WINDOW_SIZE // 2, WINDOW_SIZE // 2 + 50)
        )
        screen.blit(restart_text, restart_rect)

    # 4. Update Display
    pygame.display.flip()
    clock.tick(SPEED)  # Controls game speed

pygame.quit()
sys.exit()
