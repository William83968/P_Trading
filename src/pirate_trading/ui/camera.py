"""World-space camera used by the map presentation."""

from __future__ import annotations

from dataclasses import dataclass

import pygame


@dataclass(slots=True)
class Camera:
    """Convert between logical map coordinates and a clipped screen viewport."""

    world_size: tuple[int, int]
    viewport: pygame.Rect
    minimum_zoom: float = 1.0
    maximum_zoom: float = 3.0
    zoom: float = 1.0
    center_x: float = 500.0
    center_y: float = 353.0
    following: bool = False

    def set_viewport(self, viewport: pygame.Rect) -> None:
        self.viewport = viewport.copy()
        self._clamp()

    def world_to_screen(self, point: tuple[float, float]) -> tuple[float, float]:
        x = self.viewport.centerx + (point[0] - self.center_x) * self.zoom
        y = self.viewport.centery + (point[1] - self.center_y) * self.zoom
        return x, y

    def screen_to_world(self, point: tuple[float, float]) -> tuple[float, float]:
        x = self.center_x + (point[0] - self.viewport.centerx) / self.zoom
        y = self.center_y + (point[1] - self.viewport.centery) / self.zoom
        return x, y

    def zoom_at(self, factor: float, screen_point: tuple[float, float]) -> None:
        before = self.screen_to_world(screen_point)
        self.zoom = max(
            self.minimum_zoom,
            min(self.maximum_zoom, self.zoom * factor),
        )
        self.zoom = round(self.zoom * 20) / 20
        after = self.screen_to_world(screen_point)
        self.center_x += before[0] - after[0]
        self.center_y += before[1] - after[1]
        self.following = False
        self._clamp()

    def pan(self, dx: float, dy: float) -> None:
        self.center_x -= dx / self.zoom
        self.center_y -= dy / self.zoom
        self.following = False
        self._clamp()

    def frame_points(
        self,
        points: list[tuple[float, float]],
        *,
        padding: float = 80.0,
        maximum_zoom: float | None = None,
    ) -> None:
        if not points:
            return
        left = min(point[0] for point in points) - padding
        right = max(point[0] for point in points) + padding
        top = min(point[1] for point in points) - padding
        bottom = max(point[1] for point in points) + padding
        self.center_x = (left + right) / 2
        self.center_y = (top + bottom) / 2
        fit = min(
            self.viewport.width / max(1.0, right - left),
            self.viewport.height / max(1.0, bottom - top),
        )
        upper = self.maximum_zoom if maximum_zoom is None else min(self.maximum_zoom, maximum_zoom)
        self.zoom = max(self.minimum_zoom, min(upper, fit))
        self.following = False
        self._clamp()

    def follow(self, point: tuple[float, float], smoothing: float = 1.0) -> None:
        amount = max(0.0, min(1.0, smoothing))
        self.center_x += (point[0] - self.center_x) * amount
        self.center_y += (point[1] - self.center_y) * amount
        self.following = True
        self._clamp()

    def source_rect(self) -> pygame.Rect:
        width = self.viewport.width / self.zoom
        height = self.viewport.height / self.zoom
        return pygame.Rect(
            round(self.center_x - width / 2),
            round(self.center_y - height / 2),
            max(1, round(width)),
            max(1, round(height)),
        )

    def _clamp(self) -> None:
        world_width, world_height = self.world_size
        half_width = min(world_width / 2, self.viewport.width / (2 * self.zoom))
        half_height = min(world_height / 2, self.viewport.height / (2 * self.zoom))
        self.center_x = min(world_width - half_width, max(half_width, self.center_x))
        self.center_y = min(world_height - half_height, max(half_height, self.center_y))
