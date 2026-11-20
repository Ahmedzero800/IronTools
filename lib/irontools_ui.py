# -*- coding: utf-8 -*-
"""IronTools dark UI: modern dialogs for IronTools buttons.

ask_number(...)   -> float or None   (input card with validation)
ask_form(...)     -> dict or None    (several fields + toggle switches)
show_result(...)  -> None            (success / warning / error card)
alert(...)        -> None            (short message card)
"""

# ---------------------------------------------------------------
# Dark "card" style dialogs built with WPF (XAML), used instead of
# pyRevit's default forms.alert / ask_for_string.
# Status colours:
#   success -> green check
#   warning -> amber "!"   (finished, but something was skipped)
#   error   -> red  "x"    (nothing / not everything worked)
#   info    -> purple       (input dialogs)
# ---------------------------------------------------------------

import clr
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")
clr.AddReference("System.Xaml")

from System.Windows import Visibility, Thickness
from System.Windows.Markup import XamlReader
from System.Windows.Input import Key
from System.Windows.Controls import Button
from System.Windows.Interop import WindowInteropHelper

# ---------- colour palette ----------
STATUS = {
    "success": {"color": "#22C55E", "soft": "#1A22C55E",
                "path": "M15,27 L23,35 L38,18", "label": "Done"},
    "warning": {"color": "#F5A524", "soft": "#1AF5A524",
                "path": "M26,15 L26,29 M26,36 L26,37", "label": "Done with notes"},
    "error":   {"color": "#F04461", "soft": "#1AF04461",
                "path": "M18,18 L34,34 M34,18 L18,34", "label": "Failed"},
    "lift":    {"color": "#8B6CFA", "soft": "#268B6CFA",
                "path": "M26,37 L26,16 M17,25 L26,16 L35,25", "label": ""},
    "height":  {"color": "#8B6CFA", "soft": "#268B6CFA",
                "path": "M16,14 L36,14 M26,38 L26,21 M18,29 L26,21 L34,29", "label": ""},
    "route":   {"color": "#8B6CFA", "soft": "#268B6CFA",
                "path": "M15,38 L15,25 L31,25 L31,14 M26,19 L31,14 L36,19", "label": ""},
    "branch":  {"color": "#8B6CFA", "soft": "#268B6CFA",
                "path": "M13,30 L39,30 M26,30 L26,14 M21,19 L26,14 L31,19", "label": ""},
    "settings": {"color": "#8B6CFA", "soft": "#268B6CFA",
                 "path": "M14,18 L38,18 M14,26 L38,26 M14,34 L38,34 M20,15 L20,21 M32,23 L32,29 M24,31 L24,37",
                 "label": ""},
    "sprinkler": {"color": "#8B6CFA", "soft": "#268B6CFA",
                  "path": "M14,14 L38,14 M26,14 L26,25 M20,25 L32,25 M19,31 L16,37 M26,31 L26,38 M33,31 L36,37",
                  "label": ""},
}


def _esc(text):
    """Escape text for XAML."""
    text = u"{}".format(text)
    return (text.replace(u"&", u"&amp;").replace(u"<", u"&lt;")
                .replace(u">", u"&gt;").replace(u'"', u"&quot;"))


# ---------- shared window shell (styles + card) ----------
_SHELL = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        WindowStyle="None" AllowsTransparency="True" Background="Transparent"
        ResizeMode="NoResize" SizeToContent="WidthAndHeight"
        WindowStartupLocation="CenterScreen" ShowInTaskbar="False" Topmost="True"
        FontFamily="Segoe UI" Title="__TITLE__">
  <Window.Resources>
    <Style x:Key="Primary" TargetType="Button">
      <Setter Property="Foreground" Value="White"/>
      <Setter Property="FontSize" Value="14"/>
      <Setter Property="FontWeight" Value="SemiBold"/>
      <Setter Property="Cursor" Value="Hand"/>
      <Setter Property="MinWidth" Value="120"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border x:Name="bd" CornerRadius="21" Padding="24,10">
              <Border.Background>
                <LinearGradientBrush StartPoint="0,0" EndPoint="1,0">
                  <GradientStop Color="#7457F5" Offset="0"/>
                  <GradientStop Color="#A06CFB" Offset="1"/>
                </LinearGradientBrush>
              </Border.Background>
              <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter TargetName="bd" Property="Opacity" Value="0.85"/>
              </Trigger>
              <Trigger Property="IsPressed" Value="True">
                <Setter TargetName="bd" Property="Opacity" Value="0.7"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>
    <Style x:Key="Ghost" TargetType="Button">
      <Setter Property="Foreground" Value="#9AA6C1"/>
      <Setter Property="FontSize" Value="14"/>
      <Setter Property="Cursor" Value="Hand"/>
      <Setter Property="MinWidth" Value="100"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border x:Name="bd" CornerRadius="21" Padding="22,10" Background="#1A2540">
              <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter TargetName="bd" Property="Background" Value="#23304F"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>
    <Style x:Key="Chip" TargetType="Button">
      <Setter Property="Foreground" Value="#C9B8FF"/>
      <Setter Property="FontSize" Value="12"/>
      <Setter Property="Cursor" Value="Hand"/>
      <Setter Property="Margin" Value="0,0,8,0"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border x:Name="bd" CornerRadius="12" Padding="12,5" Background="#1F1B3A"
                    BorderBrush="#3A2F72" BorderThickness="1">
              <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter TargetName="bd" Property="Background" Value="#2C2456"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>
    <Style x:Key="Toggle" TargetType="CheckBox">
      <Setter Property="Foreground" Value="#C8D0E2"/>
      <Setter Property="FontSize" Value="13"/>
      <Setter Property="Cursor" Value="Hand"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="CheckBox">
            <Grid Background="Transparent">
              <Grid.ColumnDefinitions>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="Auto"/>
              </Grid.ColumnDefinitions>
              <ContentPresenter VerticalAlignment="Center" Margin="0,0,16,0"/>
              <Border x:Name="track" Grid.Column="1" Width="42" Height="24" CornerRadius="12"
                      Background="#26324D" VerticalAlignment="Center">
                <Ellipse x:Name="knob" Width="18" Height="18" Fill="#8B97B0"
                         HorizontalAlignment="Left" Margin="3,0,0,0"/>
              </Border>
            </Grid>
            <ControlTemplate.Triggers>
              <Trigger Property="IsChecked" Value="True">
                <Setter TargetName="track" Property="Background" Value="#7457F5"/>
                <Setter TargetName="knob" Property="HorizontalAlignment" Value="Right"/>
                <Setter TargetName="knob" Property="Margin" Value="0,0,3,0"/>
                <Setter TargetName="knob" Property="Fill" Value="#FFFFFF"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>
    <Style x:Key="Close" TargetType="Button">
      <Setter Property="Cursor" Value="Hand"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border x:Name="bd" Width="30" Height="30" CornerRadius="15" Background="Transparent">
              <Path Data="M10,10 L20,20 M20,10 L10,20" Stroke="#6B7896" StrokeThickness="2"
                    StrokeStartLineCap="Round" StrokeEndLineCap="Round"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter TargetName="bd" Property="Background" Value="#1A2540"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>
  </Window.Resources>

  <Border x:Name="Root" Margin="18" CornerRadius="26" Background="#0E1525"
          BorderBrush="#1F2A44" BorderThickness="1">
    <Border.Effect>
      <DropShadowEffect BlurRadius="28" ShadowDepth="0" Opacity="0.6" Color="#000000"/>
    </Border.Effect>
    <StackPanel Margin="28,22,28,24" Width="__WIDTH__">

      <!-- header: icon badge + title + close -->
      <Grid>
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="Auto"/>
          <ColumnDefinition Width="*"/>
          <ColumnDefinition Width="Auto"/>
        </Grid.ColumnDefinitions>
        <Border Width="52" Height="52" CornerRadius="17" Background="__SOFT__">
          <Canvas Width="52" Height="52">
            <Path Data="__ICON__" Stroke="__COLOR__" StrokeThickness="3.4"
                  StrokeStartLineCap="Round" StrokeEndLineCap="Round" StrokeLineJoin="Round"/>
          </Canvas>
        </Border>
        <StackPanel Grid.Column="1" Margin="16,2,0,0" VerticalAlignment="Center">
          <TextBlock Text="__TITLE__" Foreground="White" FontSize="19" FontWeight="SemiBold"/>
          <TextBlock Text="__SUBTITLE__" Foreground="__SUBCOLOR__" FontSize="12.5" Margin="0,3,0,0"
                     TextWrapping="Wrap"/>
        </StackPanel>
        <Button x:Name="CloseBtn" Grid.Column="2" Style="{StaticResource Close}"
                VerticalAlignment="Top" IsCancel="True"/>
      </Grid>

      __BODY__

    </StackPanel>
  </Border>
</Window>
"""


def _rows_xaml(rows):
    """Info card: list of (label, value)."""
    if not rows:
        return u""
    items = []
    for i, (label, value) in enumerate(rows):
        top = u"0" if i == 0 else u"10"
        items.append(
            u'<Grid Margin="0,{t},0,0"><Grid.ColumnDefinitions><ColumnDefinition Width="Auto"/>'
            u'<ColumnDefinition Width="*"/></Grid.ColumnDefinitions>'
            u'<TextBlock Text="{l}" Foreground="#7F8BA7" FontSize="12.5"/>'
            u'<TextBlock Grid.Column="1" Text="{v}" Foreground="#E6EAF4" FontSize="12.5" '
            u'FontWeight="SemiBold" TextAlignment="Right" TextWrapping="Wrap" Margin="16,0,0,0"/>'
            u'</Grid>'.format(t=top, l=_esc(label), v=_esc(value)))
    return (u'<Border Margin="0,20,0,0" CornerRadius="16" Background="#151F35" Padding="18,14">'
            u'<StackPanel>{}</StackPanel></Border>'.format(u"".join(items)))


def _stats_xaml(stats, accent):
    """Stat tiles: list of (value, label)."""
    if not stats:
        return u""
    tiles = []
    for i, (value, label) in enumerate(stats):
        margin = u"0,0,0,0" if i == len(stats) - 1 else u"0,0,10,0"
        color = accent if i == 0 else u"#FFFFFF"
        tiles.append(
            u'<Border Margin="{m}" CornerRadius="16" Background="#151F35" Padding="14,14">'
            u'<StackPanel><TextBlock Text="{v}" Foreground="{c}" FontSize="22" FontWeight="Bold"/>'
            u'<TextBlock Text="{l}" Foreground="#7F8BA7" FontSize="12" Margin="0,2,0,0"/>'
            u'</StackPanel></Border>'.format(m=margin, v=_esc(value), l=_esc(label), c=color))
    return (u'<UniformGrid Rows="1" Columns="{n}" Margin="0,20,0,0">{t}</UniformGrid>'
            .format(n=len(stats), t=u"".join(tiles)))


def _details_xaml(details):
    """Small note lines under the tiles."""
    if not details:
        return u""
    lines = u"".join(
        u'<TextBlock Text="{}" Foreground="#9AA6C1" FontSize="12.5" TextWrapping="Wrap" '
        u'Margin="0,6,0,0"/>'.format(_esc(u"•  " + d)) for d in details)
    return u'<StackPanel Margin="2,14,0,0">{}</StackPanel>'.format(lines)


def _build(title, subtitle, icon_key, body, width=380, subcolor=u"#7F8BA7"):
    st = STATUS[icon_key]
    xaml = (_SHELL.replace(u"__TITLE__", _esc(title))
                  .replace(u"__SUBTITLE__", _esc(subtitle or u""))
                  .replace(u"__SUBCOLOR__", subcolor)
                  .replace(u"__WIDTH__", str(width))
                  .replace(u"__SOFT__", st["soft"])
                  .replace(u"__COLOR__", st["color"])
                  .replace(u"__ICON__", st["path"])
                  .replace(u"__BODY__", body))
    win = XamlReader.Parse(xaml)

    # Keep the dialog on top of Revit
    try:
        from pyrevit import HOST_APP
        WindowInteropHelper(win).Owner = HOST_APP.proc_window
    except Exception:
        pass

    # Drag the card with the mouse
    def _drag(sender, args):
        try:
            win.DragMove()
        except Exception:
            pass
    win.FindName("Root").MouseLeftButtonDown += _drag
    win.FindName("CloseBtn").Click += lambda s, a: win.Close()
    return win


# =================================================================
# Public: input dialog
# =================================================================
def ask_number(title, subtitle=u"", rows=None, default=u"", unit=u"mm",
               label=u"Value", hint=u"", chips=None, icon=u"lift",
               ok_text=u"Apply", allow_zero=True):
    """Show a dark input card and return the typed number (float), or None if cancelled.

    rows  : list of (label, value) shown in an info card
    chips : list of quick values (strings) the user can click to fill the box
    """
    chips_xaml = u""
    if chips:
        chips_xaml = u'<WrapPanel x:Name="Chips" Margin="0,12,0,0"/>'

    body = _rows_xaml(rows) + (
        u'<TextBlock Text="{lbl}" Foreground="#9AA6C1" FontSize="12.5" Margin="2,20,0,8"/>'
        u'<Border x:Name="InputBox" CornerRadius="16" Background="#151F35" BorderBrush="#7457F5" '
        u'BorderThickness="1.5" Padding="18,8">'
        u'<Grid><Grid.ColumnDefinitions><ColumnDefinition Width="*"/><ColumnDefinition Width="Auto"/>'
        u'</Grid.ColumnDefinitions>'
        u'<TextBox x:Name="ValueBox" Background="Transparent" BorderThickness="0" Foreground="White" '
        u'CaretBrush="White" FontSize="24" FontWeight="SemiBold" VerticalContentAlignment="Center" '
        u'SelectionBrush="#7457F5"/>'
        u'<TextBlock Grid.Column="1" Text="{unit}" Foreground="#8B6CFA" FontSize="15" '
        u'FontWeight="SemiBold" VerticalAlignment="Center" Margin="10,0,0,0"/></Grid></Border>'
        u'<TextBlock x:Name="HintText" Text="{hint}" Foreground="#6B7896" FontSize="12" '
        u'Margin="2,8,0,0" TextWrapping="Wrap"/>'
        u'<TextBlock x:Name="ErrorText" Foreground="#F04461" FontSize="12" Margin="2,4,0,0" '
        u'Visibility="Collapsed" TextWrapping="Wrap"/>'
        u'{chips}'
        u'<StackPanel Orientation="Horizontal" HorizontalAlignment="Right" Margin="0,22,0,0">'
        u'<Button x:Name="CancelBtn" Style="{{StaticResource Ghost}}" Content="Cancel" Margin="0,0,10,0"/>'
        u'<Button x:Name="OkBtn" Style="{{StaticResource Primary}}" Content="{ok}" IsDefault="True"/>'
        u'</StackPanel>'
    ).format(lbl=_esc(label), unit=_esc(unit), hint=_esc(hint), chips=chips_xaml, ok=_esc(ok_text))

    win = _build(title, subtitle, icon, body)
    box = win.FindName("ValueBox")
    err = win.FindName("ErrorText")
    state = {"value": None}

    box.Text = u"{}".format(default)

    # Quick-value chips
    if chips:
        panel = win.FindName("Chips")
        style = win.FindResource("Chip")
        for c in chips:
            b = Button()
            b.Content = u"{} {}".format(c, unit)
            b.Style = style
            b.Tag = c

            def _fill(sender, args):
                box.Text = u"{}".format(sender.Tag).replace(u"+", u"")
                box.Focus()
                box.SelectAll()
            b.Click += _fill
            panel.Children.Add(b)

    def _ok(sender, args):
        text = (box.Text or u"").strip().replace(u",", u".")
        try:
            value = float(text)
        except Exception:
            err.Text = u"Please enter a valid number."
            err.Visibility = Visibility.Visible
            box.Focus()
            box.SelectAll()
            return
        if not allow_zero and value == 0:
            err.Text = u"The value cannot be zero."
            err.Visibility = Visibility.Visible
            return
        state["value"] = value
        win.Close()

    def _changed(sender, args):
        err.Visibility = Visibility.Collapsed

    win.FindName("OkBtn").Click += _ok
    win.FindName("CancelBtn").Click += lambda s, a: win.Close()
    box.TextChanged += _changed

    def _loaded(sender, args):
        box.Focus()
        box.SelectAll()
    win.Loaded += _loaded

    win.ShowDialog()
    return state["value"]


# =================================================================
# Public: result dialog
# =================================================================
def show_result(title, status=u"success", headline=u"", stats=None, details=None,
                button_text=u"Got it"):
    """Show a result card.

    status : 'success' (green check), 'warning' (amber), 'error' (red)
    stats  : list of (value, label) tiles, e.g. [("19", "Elements moved")]
    details: list of extra note lines
    """
    st = STATUS.get(status, STATUS["success"])
    sub = headline or st["label"]
    button_xaml = (
        u'<StackPanel Orientation="Horizontal" HorizontalAlignment="Right" Margin="0,22,0,0">'
        u'<Button x:Name="OkBtn" Style="{{StaticResource Primary}}" Content="{b}" IsDefault="True"/>'
        u'</StackPanel>'
    ).format(b=_esc(button_text))
    body = _stats_xaml(stats, st["color"]) + _details_xaml(details) + button_xaml

    win = _build(title, sub, status if status in STATUS else u"success", body,
                 subcolor=st["color"])
    win.FindName("OkBtn").Click += lambda s, a: win.Close()
    win.ShowDialog()


# =================================================================
# Public: short message (replaces forms.alert / TaskDialog.Show)
# =================================================================
def alert(title, message, status=u"warning", headline=None):
    """One-line message card. status: 'success', 'warning' or 'error'."""
    default_head = {u"success": u"Done", u"warning": u"Heads up", u"error": u"Something went wrong"}
    show_result(title, status, headline or default_head.get(status, u""),
                details=[message] if message else None)


# =================================================================
# Public: multi-field form (text fields + toggle switches)
# =================================================================
def ask_form(title, subtitle=u"", fields=None, toggles=None, rows=None, note=u"",
             icon=u"settings", ok_text=u"OK", validate=None, width=400):
    """Dark form with several inputs. Returns a dict, or None if cancelled.

    fields : list of dicts {key, label, value, unit (opt), hint (opt), enabled (opt, default True)}
    toggles: list of dicts {key, label, value}
    rows   : optional info card, list of (label, value)
    validate(values) -> error string (dialog stays open and shows it)
                        or a dict / any non-string value (returned to the caller)
    Without validate, the raw dict {key: text or bool} is returned.
    """
    fields = fields or []
    toggles = toggles or []

    # --- text fields card ---
    field_parts = []
    for i, f in enumerate(fields):
        enabled = f.get("enabled", True)
        top = u"0" if i == 0 else u"12"
        hint = f.get("hint", u"")
        hint_xaml = (u'<TextBlock Text="{}" Foreground="#6B7896" FontSize="11.5" Margin="0,2,0,0" '
                     u'TextWrapping="Wrap"/>'.format(_esc(hint)) if hint else u"")
        field_parts.append(
            u'<Grid Margin="0,{top},0,0" Opacity="{op}">'
            u'<Grid.ColumnDefinitions><ColumnDefinition Width="*"/><ColumnDefinition Width="150"/>'
            u'</Grid.ColumnDefinitions>'
            u'<StackPanel VerticalAlignment="Center" Margin="0,0,12,0">'
            u'<TextBlock Text="{label}" Foreground="#C8D0E2" FontSize="13"/>{hint}</StackPanel>'
            u'<Border Grid.Column="1" CornerRadius="12" Background="#0E1525" BorderBrush="{bb}" '
            u'BorderThickness="1.3" Padding="12,4">'
            u'<Grid><Grid.ColumnDefinitions><ColumnDefinition Width="*"/><ColumnDefinition Width="Auto"/>'
            u'</Grid.ColumnDefinitions>'
            u'<TextBox x:Name="F_{key}" IsEnabled="{en}" Background="Transparent" BorderThickness="0" '
            u'Foreground="White" CaretBrush="White" FontSize="15" FontWeight="SemiBold" '
            u'SelectionBrush="#7457F5" VerticalContentAlignment="Center"/>'
            u'<TextBlock Grid.Column="1" Text="{unit}" Foreground="#8B6CFA" FontSize="12.5" '
            u'FontWeight="SemiBold" VerticalAlignment="Center" Margin="6,0,0,0"/></Grid></Border>'
            u'</Grid>'.format(
                top=top, op=(u"1" if enabled else u"0.4"), label=_esc(f["label"]), hint=hint_xaml,
                bb=(u"#5B47C9" if enabled else u"#26324D"), key=f["key"],
                en=(u"True" if enabled else u"False"), unit=_esc(f.get("unit", u""))))
    fields_xaml = u""
    if field_parts:
        fields_xaml = (u'<Border Margin="0,20,0,0" CornerRadius="16" Background="#151F35" '
                       u'Padding="18,16"><StackPanel>{}</StackPanel></Border>'.format(u"".join(field_parts)))

    # --- toggle switches card ---
    toggle_parts = []
    for i, t in enumerate(toggles):
        top = u"0" if i == 0 else u"12"
        toggle_parts.append(
            u'<CheckBox x:Name="T_{key}" Style="{{StaticResource Toggle}}" Margin="0,{top},0,0">'
            u'<TextBlock Text="{label}" TextWrapping="Wrap" Foreground="#C8D0E2"/></CheckBox>'
            .format(key=t["key"], top=top, label=_esc(t["label"])))
    toggles_xaml = u""
    if toggle_parts:
        toggles_xaml = (u'<Border Margin="0,12,0,0" CornerRadius="16" Background="#151F35" '
                        u'Padding="18,14"><StackPanel>{}</StackPanel></Border>'.format(u"".join(toggle_parts)))

    note_xaml = (u'<TextBlock Text="{}" Foreground="#6B7896" FontSize="12" TextWrapping="Wrap" '
                 u'Margin="2,12,0,0"/>'.format(_esc(note)) if note else u"")

    buttons_xaml = (
        u'<TextBlock x:Name="ErrorText" Foreground="#F04461" FontSize="12" Margin="2,10,0,0" '
        u'Visibility="Collapsed" TextWrapping="Wrap"/>'
        u'<StackPanel Orientation="Horizontal" HorizontalAlignment="Right" Margin="0,22,0,0">'
        u'<Button x:Name="CancelBtn" Style="{{StaticResource Ghost}}" Content="Cancel" Margin="0,0,10,0"/>'
        u'<Button x:Name="OkBtn" Style="{{StaticResource Primary}}" Content="{ok}" IsDefault="True"/>'
        u'</StackPanel>'
    ).format(ok=_esc(ok_text))
    body = _rows_xaml(rows) + fields_xaml + toggles_xaml + note_xaml + buttons_xaml

    win = _build(title, subtitle, icon, body, width=width)
    err = win.FindName("ErrorText")
    state = {"value": None}

    boxes = {}
    for f in fields:
        box = win.FindName("F_" + f["key"])
        box.Text = u"{}".format(f.get("value", u""))
        boxes[f["key"]] = box
    checks = {}
    for t in toggles:
        cb = win.FindName("T_" + t["key"])
        cb.IsChecked = bool(t.get("value"))
        checks[t["key"]] = cb

    def _collect():
        values = {}
        for k, box in boxes.items():
            values[k] = (box.Text or u"").strip()
        for k, cb in checks.items():
            values[k] = bool(cb.IsChecked)
        return values

    def _ok(sender, args):
        values = _collect()
        outcome = validate(values) if validate else values
        if isinstance(outcome, basestring):
            err.Text = outcome
            err.Visibility = Visibility.Visible
            return
        state["value"] = outcome
        win.Close()

    win.FindName("OkBtn").Click += _ok
    win.FindName("CancelBtn").Click += lambda s, a: win.Close()

    def _loaded(sender, args):
        for f in fields:
            if f.get("enabled", True):
                boxes[f["key"]].Focus()
                boxes[f["key"]].SelectAll()
                break
    win.Loaded += _loaded

    win.ShowDialog()
    return state["value"]
