"""Responsive region calculation for the pygame application shell."""

from dataclasses import dataclass

import pygame


def message_paper_content_rect(paper_rect: pygame.Rect) -> pygame.Rect:
    """Return the writable area of ``message_paper.png`` at any scaled size.

    The source image includes transparent space and heavy rolled edges.  Keep
    layout tied to proportions of the rendered paper instead of to margins
    copied from its original 500px size.
    """

    left = round(paper_rect.width * 0.18)
    right = round(paper_rect.width * 0.18)
    top = round(paper_rect.height * 0.19)
    bottom = round(paper_rect.height * 0.31)
    return pygame.Rect(
        paper_rect.x + left,
        paper_rect.y + top,
        paper_rect.width - left - right,
        paper_rect.height - top - bottom,
    )


@dataclass(frozen=True, slots=True)
class UILayout:
    status_bar: pygame.Rect
    navigation_rail: pygame.Rect
    map_viewport: pygame.Rect
    inspector: pygame.Rect
    action_tray: pygame.Rect

    @classmethod
    def build(
        cls,
        window_size: tuple[int, int],
        *,
        inspector_collapsed: bool = False,
    ) -> "UILayout":
        width, height = window_size
        status_height = 56
        navigation_width = 64
        action_height = 88
        inspector_width = 0 if inspector_collapsed else 320
        workspace_width = width - navigation_width - inspector_width
        workspace_height = height - status_height - action_height
        return cls(
            status_bar=pygame.Rect(0, 0, width, status_height),
            navigation_rail=pygame.Rect(0, status_height, navigation_width, height - status_height),
            map_viewport=pygame.Rect(
                navigation_width,
                status_height,
                workspace_width,
                workspace_height,
            ),
            inspector=pygame.Rect(
                width - inspector_width,
                status_height,
                inspector_width,
                height - status_height,
            ),
            action_tray=pygame.Rect(
                navigation_width,
                height - action_height,
                workspace_width,
                action_height,
            ),
        )

    def regions_do_not_overlap(self) -> bool:
        primary = [
            self.status_bar,
            self.navigation_rail,
            self.map_viewport,
            self.inspector,
        ]
        return all(
            not first.colliderect(second)
            for index, first in enumerate(primary)
            for second in primary[index + 1 :]
            if first.width and second.width
        )
