# -*- coding: utf-8 -*-
"""Live preview for Smart Tag.

While you pick the point, a see-through window lies exactly over the Revit view and
draws where the tags and leaders will go, following the mouse. The window lets every
click pass through to Revit, so picking works as usual.

How it maps the mouse to the model:
  UIView.GetWindowRectangle()  -> the view's rectangle on screen (pixels)
  UIView.GetZoomCorners()      -> the model points at its bottom-left / top-right corners
  linear interpolation between the two gives model (u, v) for any screen pixel.
"""

import time
import clr
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")
clr.AddReference("System.Windows.Forms")
clr.AddReference("System.Drawing")

from System import TimeSpan, IntPtr
from System.Windows import Window, WindowStyle, ResizeMode, Point, TextTrimming
from System.Windows.Controls import Canvas, TextBlock
from System.Windows.Media import SolidColorBrush, Color, PointCollection
from System.Windows.Shapes import Rectangle, Polyline, Ellipse
from System.Windows.Threading import DispatcherTimer, DispatcherPriority
from System.Windows.Interop import WindowInteropHelper
from System.Windows.Forms import Cursor
from System.Drawing import Graphics


def _brush(a, r, g, b):
    br = SolidColorBrush(Color.FromArgb(a, r, g, b))
    br.Freeze()
    return br


TAG_FILL = _brush(70, 116, 87, 245)
TAG_EDGE = _brush(255, 139, 108, 250)
TEXT = _brush(255, 255, 255, 255)
LEADER = _brush(255, 245, 165, 36)
WARN = _brush(255, 240, 68, 97)


def _make_click_through(win):
    """Let mouse clicks pass through the overlay to Revit (Win32 extended styles)."""
    try:
        import ctypes
        hwnd = WindowInteropHelper(win).Handle
        h = int(hwnd.ToInt64())
        GWL_EXSTYLE = -20
        WS_EX_TRANSPARENT, WS_EX_LAYERED = 0x20, 0x80000
        WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x80, 0x08000000
        u32 = ctypes.windll.user32
        style = u32.GetWindowLongW(h, GWL_EXSTYLE)
        u32.SetWindowLongW(h, GWL_EXSTYLE,
                           style | WS_EX_TRANSPARENT | WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)
    except Exception:
        pass


class LivePreview(object):
    """layout_fn(uv, optimize) -> layout dict from smarttag.compute_layout."""

    def __init__(self, uidoc, view, frame, layout_fn, optimize_on_pause=True):
        self.frame = frame
        self.layout_fn = layout_fn
        self.optimize_on_pause = optimize_on_pause
        self.uiview = None
        for uv in uidoc.GetOpenUIViews():
            if uv.ViewId == view.Id:
                self.uiview = uv
                break
        self.win = None
        self.canvas = None
        self.timer = None
        self.map = None
        self.last_cursor = None
        self.last_move = 0.0
        self.optimized = False
        g = Graphics.FromHwnd(IntPtr.Zero)
        self.dpi = g.DpiX / 96.0
        g.Dispose()

    # ---------- screen <-> model ----------
    def _read_map(self):
        try:
            r = self.uiview.GetWindowRectangle()
            c = self.uiview.GetZoomCorners()
            a, b = self.frame.to2d(c[0]), self.frame.to2d(c[1])
            self.map = (r.Left, r.Top, r.Right, r.Bottom,
                        min(a[0], b[0]), max(a[0], b[0]), min(a[1], b[1]), max(a[1], b[1]))
        except Exception:
            pass  # keep the last good map (e.g. if Revit refuses the call mid-pick)
        return self.map

    def _to_screen(self, uv):
        L, T, R, B, u0, u1, v0, v1 = self.map
        x = (uv[0] - u0) / (u1 - u0) * (R - L) / self.dpi
        y = (v1 - uv[1]) / (v1 - v0) * (B - T) / self.dpi
        return x, y

    # ---------- window ----------
    def start(self):
        if self.uiview is None or self._read_map() is None:
            return False
        L, T, R, B = self.map[:4]
        win = Window()
        win.WindowStyle = WindowStyle.None
        win.AllowsTransparency = True
        win.Background = _brush(0, 0, 0, 0)
        win.ResizeMode = ResizeMode.NoResize
        win.ShowInTaskbar = False
        win.ShowActivated = False
        win.Topmost = True
        win.IsHitTestVisible = False
        win.Left, win.Top = L / self.dpi, T / self.dpi
        win.Width, win.Height = (R - L) / self.dpi, (B - T) / self.dpi
        self.canvas = Canvas()
        self.canvas.IsHitTestVisible = False
        win.Content = self.canvas
        win.SourceInitialized += lambda s, a: _make_click_through(win)
        self.win = win
        win.Show()
        self.timer = DispatcherTimer(DispatcherPriority.Render)
        self.timer.Interval = TimeSpan.FromMilliseconds(40)
        self.timer.Tick += self._tick
        self.timer.Start()
        return True

    def stop(self):
        try:
            if self.timer is not None:
                self.timer.Stop()
        except Exception:
            pass
        try:
            if self.win is not None:
                self.win.Close()
        except Exception:
            pass

    # ---------- update loop ----------
    def _tick(self, sender, args):
        try:
            old = self.map
            m = self._read_map()
            if m is None:
                return
            L, T, R, B, u0, u1, v0, v1 = m
            if old is None or old[:4] != m[:4]:
                self.win.Left, self.win.Top = L / self.dpi, T / self.dpi
                self.win.Width, self.win.Height = (R - L) / self.dpi, (B - T) / self.dpi
            p = Cursor.Position
            if not (L <= p.X <= R and T <= p.Y <= B):
                self.canvas.Children.Clear()
                self.last_cursor = None
                return
            uv = (u0 + (p.X - L) / float(R - L) * (u1 - u0),
                  v1 - (p.Y - T) / float(B - T) * (v1 - v0))
            now = time.time()
            moved = (self.last_cursor is None or abs(p.X - self.last_cursor[0]) > 1 or
                     abs(p.Y - self.last_cursor[1]) > 1 or old != m)
            if moved:
                self.last_cursor = (p.X, p.Y)
                self.last_move = now
                self.optimized = False
                self._draw(self.layout_fn(uv, False))
            elif self.optimize_on_pause and not self.optimized and now - self.last_move > 0.35:
                self.optimized = True
                self._draw(self.layout_fn(uv, True))
        except Exception:
            pass

    def _draw(self, layout):
        cv = self.canvas
        cv.Children.Clear()
        for row in layout["rows"]:
            # leader: end -> elbow -> tag
            pts = PointCollection()
            for q in (row["end"], row["elbow"], row["tip"]):
                x, y = self._to_screen(q)
                pts.Add(Point(x, y))
            pl = Polyline()
            pl.Points = pts
            pl.Stroke = LEADER
            pl.StrokeThickness = 1.6
            cv.Children.Add(pl)
            ex, ey = self._to_screen(row["end"])
            dot = Ellipse()
            dot.Width = dot.Height = 6
            dot.Fill = LEADER
            Canvas.SetLeft(dot, ex - 3)
            Canvas.SetTop(dot, ey - 3)
            cv.Children.Add(dot)
            # tag box with its text
            umin, umax, vmin, vmax = row["rect"]
            x0, y0 = self._to_screen((umin, vmax))
            x1, y1 = self._to_screen((umax, vmin))
            w, h = max(2.0, x1 - x0), max(2.0, y1 - y0)
            box = Rectangle()
            box.Width, box.Height = w, h
            box.Fill = TAG_FILL
            box.Stroke = TAG_EDGE
            box.StrokeThickness = 1
            box.RadiusX = box.RadiusY = 2
            Canvas.SetLeft(box, x0)
            Canvas.SetTop(box, y0)
            cv.Children.Add(box)
            if h >= 7:
                tb = TextBlock()
                tb.Text = row["item"].get("text", u"")
                tb.Foreground = TEXT
                tb.FontSize = max(6.0, h * 0.62)
                tb.Width = w
                tb.TextTrimming = TextTrimming.CharacterEllipsis
                Canvas.SetLeft(tb, x0 + 1)
                Canvas.SetTop(tb, y0 + (h - tb.FontSize * 1.33) * 0.5)
                cv.Children.Add(tb)
        if layout.get("crossings"):
            note = TextBlock()
            note.Text = u"  {} crossing(s) - stop moving to optimise  ".format(layout["crossings"])
            note.Foreground = TEXT
            note.Background = WARN
            note.FontSize = 12
            Canvas.SetLeft(note, 8)
            Canvas.SetTop(note, 8)
            cv.Children.Add(note)
