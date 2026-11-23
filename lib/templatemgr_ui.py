# -*- coding: utf-8 -*-
"""View Template Manager windows, in the IronTools dark style.

  ManagerWindow(doc, uidoc).show()   main window: template list (left) + settings (right)
  ask_paste(...)                      'Paste settings' dialog, returns (param ids, copy flags)

The styles (buttons, chips, toggles) are taken from irontools_ui so every IronTools
window looks the same.
"""

import clr
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")
clr.AddReference("System.Xaml")

from System.Collections.Generic import List
from System.Windows import Visibility, Thickness, GridLength, GridUnitType, HorizontalAlignment, \
    VerticalAlignment, TextWrapping, TextTrimming
from System.Windows.Markup import XamlReader
from System.Windows.Controls import (Grid, ColumnDefinition, TextBlock, CheckBox, Button,
                                     ListBoxItem, StackPanel, Orientation, Border)
from System.Windows.Media import SolidColorBrush, Color
from System.Windows.Shapes import Ellipse
from System.Windows.Interop import WindowInteropHelper
from Autodesk.Revit.DB import ElementId

import irontools_ui as ui
import templatemgr as tm


def _brush(hexstr):
    h = hexstr.lstrip("#")
    if len(h) == 6:
        h = "FF" + h
    b = SolidColorBrush(Color.FromArgb(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(h[6:8], 16)))
    b.Freeze()
    return b


WHITE, TEXT, MUTED, DIM = _brush("#FFFFFF"), _brush("#E6EAF4"), _brush("#7F8BA7"), _brush("#55607A")
ORANGE, PURPLE, CLEAR = _brush("#F5A524"), _brush("#A78BFA"), _brush("#00000000")

# the shared IronTools styles (Primary, Ghost, Chip, Pick, Toggle, Close)
_S = ui._SHELL
_RES = _S[_S.index(u"<Window.Resources>") + len(u"<Window.Resources>"):_S.index(u"</Window.Resources>")]

_EXTRA = u"""
    <!-- thin dark scrollbar (applies to every vertical scrollbar in the window) -->
    <Style TargetType="ScrollBar">
      <Setter Property="Width" Value="8"/>
      <Setter Property="MinWidth" Value="8"/>
      <Setter Property="Background" Value="Transparent"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="ScrollBar">
            <Border Background="#0E1525" CornerRadius="4">
              <Track x:Name="PART_Track" IsDirectionReversed="True">
                <Track.Thumb>
                  <Thumb>
                    <Thumb.Template>
                      <ControlTemplate TargetType="Thumb">
                        <Border x:Name="th" CornerRadius="4" Background="#3A4A6B" MinHeight="30"/>
                        <ControlTemplate.Triggers>
                          <Trigger Property="IsMouseOver" Value="True">
                            <Setter TargetName="th" Property="Background" Value="#7457F5"/>
                          </Trigger>
                          <Trigger Property="IsDragging" Value="True">
                            <Setter TargetName="th" Property="Background" Value="#8B6CFA"/>
                          </Trigger>
                        </ControlTemplate.Triggers>
                      </ControlTemplate>
                    </Thumb.Template>
                  </Thumb>
                </Track.Thumb>
              </Track>
            </Border>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>
    <Style x:Key="Row" TargetType="ListBoxItem">
      <Setter Property="Foreground" Value="#E6EAF4"/>
      <Setter Property="Cursor" Value="Hand"/>
      <Setter Property="HorizontalContentAlignment" Value="Stretch"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="ListBoxItem">
            <Border x:Name="bd" CornerRadius="10" Padding="10,7" Margin="0,0,0,3" Background="Transparent"
                    BorderBrush="Transparent" BorderThickness="1">
              <ContentPresenter/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter TargetName="bd" Property="Background" Value="#1A2540"/>
              </Trigger>
              <Trigger Property="IsSelected" Value="True">
                <Setter TargetName="bd" Property="Background" Value="#2C2456"/>
                <Setter TargetName="bd" Property="BorderBrush" Value="#7457F5"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>
    <Style x:Key="Check" TargetType="CheckBox">
      <Setter Property="Cursor" Value="Hand"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="CheckBox">
            <Border x:Name="box" Width="18" Height="18" CornerRadius="5" Background="#0E1525"
                    BorderBrush="#3A4A6B" BorderThickness="1.5">
              <Path x:Name="mark" Data="M3.5,8.5 L7,12 L13,4.5" Stroke="White" StrokeThickness="2.2"
                    StrokeStartLineCap="Round" StrokeEndLineCap="Round" StrokeLineJoin="Round"
                    Visibility="Collapsed"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsChecked" Value="True">
                <Setter TargetName="box" Property="Background" Value="#7457F5"/>
                <Setter TargetName="box" Property="BorderBrush" Value="#7457F5"/>
                <Setter TargetName="mark" Property="Visibility" Value="Visible"/>
              </Trigger>
              <Trigger Property="IsChecked" Value="{x:Null}">
                <Setter TargetName="box" Property="Background" Value="#4B5675"/>
                <Setter TargetName="box" Property="BorderBrush" Value="#4B5675"/>
                <Setter TargetName="mark" Property="Visibility" Value="Visible"/>
              </Trigger>
              <Trigger Property="IsEnabled" Value="False">
                <Setter TargetName="box" Property="Opacity" Value="0.35"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>
    <Style x:Key="Count" TargetType="Button">
      <Setter Property="Foreground" Value="#C9B8FF"/>
      <Setter Property="FontSize" Value="12"/>
      <Setter Property="Cursor" Value="Hand"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border x:Name="bd" CornerRadius="9" Padding="9,2" Background="#1F1B3A"
                    BorderBrush="#3A2F72" BorderThickness="1" HorizontalAlignment="Right">
              <ContentPresenter HorizontalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter TargetName="bd" Property="Background" Value="#7457F5"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>
"""

_SEARCH = u"""
<Border CornerRadius="12" Background="#0E1525" BorderBrush="#26324D" BorderThickness="1.3" Padding="12,6" __MARGIN__>
  <Grid>
    <TextBlock x:Name="__NAME__Hint" Text="__HINT__" Foreground="#55607A" FontSize="13" IsHitTestVisible="False"
               VerticalAlignment="Center"/>
    <TextBox x:Name="__NAME__" Background="Transparent" BorderThickness="0" Foreground="White" CaretBrush="White"
             FontSize="13" SelectionBrush="#7457F5" VerticalContentAlignment="Center"/>
  </Grid>
</Border>"""


def _search(name, hint, margin=u""):
    return _SEARCH.replace(u"__NAME__", name).replace(u"__HINT__", hint).replace(u"__MARGIN__", margin)


_MAIN = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        WindowStyle="None" AllowsTransparency="True" Background="Transparent"
        ResizeMode="CanResizeWithGrip" Width="1180" Height="780" MinWidth="900" MinHeight="560"
        WindowStartupLocation="CenterScreen" ShowInTaskbar="True" FontFamily="Segoe UI"
        Title="View Template Manager">
  <Window.Resources>__RES__</Window.Resources>
  <Border x:Name="Root" Margin="14" CornerRadius="22" Background="#0E1525" BorderBrush="#1F2A44" BorderThickness="1">
    <Border.Effect>
      <DropShadowEffect BlurRadius="26" ShadowDepth="0" Opacity="0.55" Color="#000000"/>
    </Border.Effect>
    <Grid Margin="26,20,26,18">
      <Grid.RowDefinitions>
        <RowDefinition Height="Auto"/>
        <RowDefinition Height="*"/>
        <RowDefinition Height="Auto"/>
      </Grid.RowDefinitions>

      <!-- header -->
      <Grid x:Name="Header" Background="Transparent">
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="Auto"/><ColumnDefinition Width="*"/><ColumnDefinition Width="Auto"/>
        </Grid.ColumnDefinitions>
        <Border Width="48" Height="48" CornerRadius="15" Background="#268B6CFA">
          <Canvas Width="52" Height="52" Margin="-2,-2,0,0">
            <Path Data="__ICON__" Stroke="#8B6CFA" StrokeThickness="3.2" StrokeLineJoin="Round"
                  StrokeStartLineCap="Round" StrokeEndLineCap="Round"/>
          </Canvas>
        </Border>
        <StackPanel Grid.Column="1" Margin="16,0,0,0" VerticalAlignment="Center">
          <TextBlock Text="View Template Manager" Foreground="White" FontSize="20" FontWeight="SemiBold"/>
          <TextBlock x:Name="DocName" Foreground="#7F8BA7" FontSize="12.5" Margin="0,3,0,0"/>
        </StackPanel>
        <Button x:Name="CloseBtn" Grid.Column="2" Style="{StaticResource Close}" VerticalAlignment="Top"/>
      </Grid>

      <!-- body -->
      <Grid Grid.Row="1" Margin="0,18,0,0">
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="5*"/><ColumnDefinition Width="16"/><ColumnDefinition Width="6*"/>
        </Grid.ColumnDefinitions>

        <!-- left: templates -->
        <Border CornerRadius="18" Background="#151F35" Padding="16,14">
          <Grid>
            <Grid.RowDefinitions>
              <RowDefinition Height="Auto"/><RowDefinition Height="Auto"/>
              <RowDefinition Height="Auto"/><RowDefinition Height="*"/>
            </Grid.RowDefinitions>
            <Grid>
              <Grid.ColumnDefinitions><ColumnDefinition Width="*"/><ColumnDefinition Width="Auto"/></Grid.ColumnDefinitions>
              __SEARCH_T__
              <CheckBox x:Name="OnlyUsed" Grid.Column="1" Style="{StaticResource Toggle}" Margin="14,0,0,0"
                        VerticalAlignment="Center"><TextBlock Text="Only used" Foreground="#C8D0E2"/></CheckBox>
            </Grid>
            <WrapPanel Grid.Row="1" Margin="0,12,0,0">
              <Button x:Name="CopyBtn" Style="{StaticResource Chip}" Content="Copy settings"/>
              <Button x:Name="PasteBtn" Style="{StaticResource Chip}" Content="Paste settings"/>
              <Button x:Name="DupBtn" Style="{StaticResource Chip}" Content="Duplicate"/>
              <Button x:Name="DelBtn" Style="{StaticResource Chip}" Content="Delete"/>
            </WrapPanel>
            <Grid Grid.Row="2" Margin="12,14,14,6">
              <Grid.ColumnDefinitions>
                <ColumnDefinition Width="18"/><ColumnDefinition Width="*"/>
                <ColumnDefinition Width="100"/><ColumnDefinition Width="56"/>
              </Grid.ColumnDefinitions>
              <TextBlock Grid.Column="1" Text="View template" Foreground="#7F8BA7" FontSize="12"/>
              <TextBlock Grid.Column="2" Text="View type" Foreground="#7F8BA7" FontSize="12"/>
              <TextBlock Grid.Column="3" Text="Views" Foreground="#7F8BA7" FontSize="12" HorizontalAlignment="Right"/>
            </Grid>
            <ListBox x:Name="TplList" Grid.Row="3" SelectionMode="Extended" Background="Transparent"
                     BorderThickness="0" ItemContainerStyle="{StaticResource Row}"
                     ScrollViewer.HorizontalScrollBarVisibility="Disabled"/>
          </Grid>
        </Border>

        <!-- right: settings -->
        <Border Grid.Column="2" CornerRadius="18" Background="#151F35" Padding="18,14">
          <Grid>
            <Grid.RowDefinitions>
              <RowDefinition Height="Auto"/><RowDefinition Height="Auto"/><RowDefinition Height="*"/>
            </Grid.RowDefinitions>
            <StackPanel>
              <TextBlock x:Name="NameLabel" Text="Template name" Foreground="#9AA6C1" FontSize="12.5" Margin="2,0,0,6"/>
              <Border x:Name="NameBorder" CornerRadius="12" Background="#0E1525" BorderBrush="#5B47C9"
                      BorderThickness="1.3" Padding="12,6">
                <TextBox x:Name="NameBox" Background="Transparent" BorderThickness="0" Foreground="White"
                         CaretBrush="White" FontSize="15" FontWeight="SemiBold" SelectionBrush="#7457F5"/>
              </Border>
              <TextBlock x:Name="MultiInfo" Foreground="#C9B8FF" FontSize="15" FontWeight="SemiBold"
                         Visibility="Collapsed" Margin="2,4,0,4"/>
            </StackPanel>
            <Grid Grid.Row="1" Margin="0,14,0,10">
              <Grid.ColumnDefinitions>
                <ColumnDefinition Width="*"/><ColumnDefinition Width="Auto"/><ColumnDefinition Width="Auto"/>
              </Grid.ColumnDefinitions>
              __SEARCH_S__
              <CheckBox x:Name="OnlyControlled" Grid.Column="1" Style="{StaticResource Toggle}" Margin="14,0,4,0"
                        VerticalAlignment="Center"><TextBlock Text="Only controlled" Foreground="#C8D0E2"/></CheckBox>
              <StackPanel Grid.Column="2" Orientation="Horizontal" Margin="10,0,0,0" VerticalAlignment="Center">
                <Button x:Name="AllOn" Style="{StaticResource Chip}" Content="Control all" ToolTip="Tick every setting shown"/>
                <Button x:Name="AllOff" Style="{StaticResource Chip}" Content="Release all" Margin="0"
                        ToolTip="Untick every setting shown"/>
              </StackPanel>
            </Grid>
            <ScrollViewer Grid.Row="2" VerticalScrollBarVisibility="Auto">
              <StackPanel x:Name="SetPanel" Margin="0,0,8,0"/>
            </ScrollViewer>
            <TextBlock x:Name="EmptyHint" Grid.Row="2" Text="Select a view template on the left"
                       Foreground="#55607A" FontSize="14" HorizontalAlignment="Center" VerticalAlignment="Center"/>
          </Grid>
        </Border>
      </Grid>

      <!-- footer -->
      <Grid Grid.Row="2" Margin="0,16,0,0">
        <Grid.ColumnDefinitions><ColumnDefinition Width="*"/><ColumnDefinition Width="Auto"/></Grid.ColumnDefinitions>
        <StackPanel VerticalAlignment="Center">
          <TextBlock x:Name="Status" Foreground="#7F8BA7" FontSize="12.5" TextWrapping="Wrap"/>
          <TextBlock x:Name="Message" Foreground="#F5A524" FontSize="12.5" TextWrapping="Wrap" Margin="0,3,0,0"/>
        </StackPanel>
        <StackPanel Grid.Column="1" Orientation="Horizontal">
          <Button x:Name="CancelBtn" Style="{StaticResource Ghost}" Content="Discard" Margin="0,0,10,0"/>
          <Button x:Name="AcceptBtn" Style="{StaticResource Primary}" Content="Accept"/>
        </StackPanel>
      </Grid>
    </Grid>
  </Border>
</Window>
"""


def _owner_to_revit(win):
    try:
        from pyrevit import HOST_APP
        WindowInteropHelper(win).Owner = HOST_APP.proc_window
    except Exception:
        pass


def _hook_search(box, hint, callback):
    def changed(s, a):
        hint.Visibility = Visibility.Collapsed if box.Text else Visibility.Visible
        callback()
    box.TextChanged += changed


def _cell(text, brush, size=13.0, col=None, bold=False, trim=True):
    tb = TextBlock()
    tb.Text = text
    tb.Foreground = brush
    tb.FontSize = size
    tb.VerticalAlignment = VerticalAlignment.Center
    if trim:
        tb.TextTrimming = TextTrimming.CharacterEllipsis
    if bold:
        from System.Windows import FontWeights
        tb.FontWeight = FontWeights.SemiBold
    if col is not None:
        Grid.SetColumn(tb, col)
    return tb


def _cols(grid, widths):
    for w in widths:
        cd = ColumnDefinition()
        cd.Width = GridLength(1, GridUnitType.Star) if w == "*" else (
            GridLength(1, GridUnitType.Auto) if w == "auto" else GridLength(w))
        grid.ColumnDefinitions.Add(cd)


# =================================================================
# Main window
# =================================================================
class ManagerWindow(object):

    def __init__(self, doc, uidoc):
        self.doc = doc
        self.uidoc = uidoc
        xaml = (_MAIN.replace(u"__RES__", _RES + _EXTRA)
                     .replace(u"__ICON__", ui.STATUS["template"]["path"])
                     .replace(u"__SEARCH_T__", _search(u"SearchT", u"Search template by name"))
                     .replace(u"__SEARCH_S__", _search(u"SearchS", u"Search setting")))
        self.win = XamlReader.Parse(xaml)
        _owner_to_revit(self.win)
        f = self.win.FindName
        self.lst, self.panel = f("TplList"), f("SetPanel")
        self.search_t, self.search_s = f("SearchT"), f("SearchS")
        self.only_used, self.only_ctrl = f("OnlyUsed"), f("OnlyControlled")
        self.name_box, self.name_border = f("NameBox"), f("NameBorder")
        self.name_label, self.multi_info = f("NameLabel"), f("MultiInfo")
        self.status, self.message, self.empty = f("Status"), f("Message"), f("EmptyHint")
        f("DocName").Text = u"{}   •   tick a setting to let the template control it".format(doc.Title)

        self.clipboard = None          # template id copied with 'Copy settings'
        self.names = {}                # pending renames {tid: name}
        self.nonc = {}                 # pending NON-controlled sets {tid: set(ids)}
        self.loading = False
        self.setting_rows = []         # (checkbox, param id) of the rows on screen
        self.cache = {}                # tid -> {pid: Setting}

        # events
        f("Header").MouseLeftButtonDown += self._drag
        f("CloseBtn").Click += lambda s, a: self._close()
        f("CancelBtn").Click += self._discard
        f("AcceptBtn").Click += self._accept
        f("CopyBtn").Click += self._copy
        f("PasteBtn").Click += self._paste
        f("DupBtn").Click += self._duplicate
        f("DelBtn").Click += self._delete
        f("AllOn").Click += lambda s, a: self._set_all(True)
        f("AllOff").Click += lambda s, a: self._set_all(False)
        _hook_search(self.search_t, f("SearchTHint"), self._fill_list)
        _hook_search(self.search_s, f("SearchSHint"), self._fill_settings)
        self.only_used.Click += lambda s, a: self._fill_list()
        self.only_ctrl.Click += lambda s, a: self._fill_settings()
        self.lst.SelectionChanged += lambda s, a: self._on_select()
        self.name_box.TextChanged += self._on_rename

        self._reload()

    # ---------- data ----------
    def _reload(self, keep=None):
        self.infos = tm.load_templates(self.doc)
        self.by_id = dict((t.id, t) for t in self.infos)
        self.cache = {}
        self._fill_list(keep)

    def _settings(self, tid):
        if tid not in self.cache:
            self.cache[tid] = tm.template_settings(self.doc, self.by_id[tid].view)
        return self.cache[tid]

    def _nonc(self, tid):
        if tid in self.nonc:
            return self.nonc[tid]
        return tm.non_controlled(self.by_id[tid].view)

    def _pending(self, tid):
        return tid in self.names or tid in self.nonc

    def _has_pending(self):
        return bool(self.names or self.nonc)

    def _selected(self):
        return [item.Tag for item in self.lst.SelectedItems]

    # ---------- left list ----------
    def _fill_list(self, keep=None):
        keep = set(keep if keep is not None else self._selected())
        q = (self.search_t.Text or u"").strip().lower()
        only = bool(self.only_used.IsChecked)
        self.loading = True
        self.lst.Items.Clear()
        shown = 0
        for t in self.infos:
            name = self.names.get(t.id, t.name)
            if q and q not in name.lower():
                continue
            if only and not t.uses:
                continue
            item = ListBoxItem()
            item.Style = self.win.FindResource("Row")
            item.Tag = t.id
            g = Grid()
            _cols(g, [18, "*", 100, 56])
            dot = Ellipse()
            dot.Width = dot.Height = 8
            dot.Fill = ORANGE if self._pending(t.id) else CLEAR
            dot.HorizontalAlignment = HorizontalAlignment.Left
            dot.ToolTip = u"Changes not applied yet"
            g.Children.Add(dot)
            g.Children.Add(_cell(name, TEXT, 13, 1))
            g.Children.Add(_cell(t.vtype, MUTED, 12, 2))
            btn = Button()
            btn.Style = self.win.FindResource("Count")
            btn.Content = str(len(t.uses))
            btn.Tag = t.id
            btn.ToolTip = u"Select the views that use this template"
            btn.Click += self._select_views
            Grid.SetColumn(btn, 3)
            g.Children.Add(btn)
            item.Content = g
            self.lst.Items.Add(item)
            if t.id in keep:
                item.IsSelected = True
            shown += 1
        self.loading = False
        self.shown = shown
        self._on_select()

    def _update_status(self):
        sel = len(self._selected())
        clip = u"empty"
        if self.clipboard in self.by_id:
            clip = self.by_id[self.clipboard].name
        self.status.Text = u"{} view templates   •   {} shown   •   {} selected   •   copied settings: {}".format(
            len(self.infos), self.shown, sel, clip)
        self.win.FindName("PasteBtn").IsEnabled = self.clipboard is not None and sel > 0

    # ---------- right panel ----------
    def _on_select(self):
        if self.loading:
            return
        sel = self._selected()
        self.loading = True
        if len(sel) == 1:
            tid = sel[0]
            self.name_border.Visibility = Visibility.Visible
            self.name_label.Visibility = Visibility.Visible
            self.multi_info.Visibility = Visibility.Collapsed
            self.name_box.Text = self.names.get(tid, self.by_id[tid].name)
        else:
            self.name_border.Visibility = Visibility.Collapsed
            self.name_label.Visibility = Visibility.Collapsed
            self.multi_info.Visibility = Visibility.Visible if sel else Visibility.Collapsed
            self.multi_info.Text = u"{} templates selected  –  changes apply to all of them".format(len(sel))
        self.loading = False
        self._fill_settings()
        self._update_status()

    def _fill_settings(self):
        sel = self._selected()
        self.panel.Children.Clear()
        self.setting_rows = []
        self.empty.Visibility = Visibility.Collapsed if sel else Visibility.Visible
        if not sel:
            return
        sets = [self._settings(t) for t in sel]
        common = set(sets[0].keys())
        for s in sets[1:]:
            common &= set(s.keys())
        q = (self.search_s.Text or u"").strip().lower()
        only = bool(self.only_ctrl.IsChecked)
        groups = {}
        for pid in common:
            st = sets[0][pid]
            if q and q not in st.name.lower():
                continue
            states = [pid not in self._nonc(t) for t in sel]
            state = True if all(states) else (False if not any(states) else None)
            if only and state is False:
                continue
            values = set(s[pid].value for s in sets)
            value = values.pop() if len(values) == 1 else u"*VARIES*"
            groups.setdefault(st.group, []).append((st, state, value))
        for gname in sorted(groups):
            head = _cell(gname.upper(), PURPLE, 11.5, bold=True)
            head.Margin = Thickness(2, 14, 0, 6)
            self.panel.Children.Add(head)
            for st, state, value in sorted(groups[gname], key=lambda x: x[0].name.lower()):
                row = Grid()
                row.Margin = Thickness(2, 3, 0, 3)
                _cols(row, [30, "*", "auto"])
                cb = CheckBox()
                cb.Style = self.win.FindResource("Check")
                cb.IsChecked = state
                cb.Tag = st.id
                cb.VerticalAlignment = VerticalAlignment.Center
                cb.ToolTip = u"Controlled by the template"
                cb.Click += self._on_check
                row.Children.Add(cb)
                row.Children.Add(_cell(st.name, TEXT, 13, 1))
                v = _cell(value, MUTED if value != u"*VARIES*" else ORANGE, 12.5, 2)
                v.MaxWidth = 260
                v.Margin = Thickness(12, 0, 0, 0)
                row.Children.Add(v)
                self.panel.Children.Add(row)
                self.setting_rows.append((cb, st.id))
        if not self.setting_rows:
            none = _cell(u"No setting matches the search / filter", DIM, 13)
            none.Margin = Thickness(2, 16, 0, 0)
            self.panel.Children.Add(none)

    def _set_controlled(self, pid, controlled):
        for tid in self._selected():
            cur = set(self._nonc(tid))
            if controlled:
                cur.discard(pid)
            else:
                cur.add(pid)
            original = tm.non_controlled(self.by_id[tid].view)
            if cur == original:
                self.nonc.pop(tid, None)
            else:
                self.nonc[tid] = cur

    def _on_check(self, sender, args):
        pid = sender.Tag
        # mixed or off -> control it in every selected template; on -> release it
        sel = self._selected()
        controlled_everywhere = all(pid not in self._nonc(t) for t in sel)
        new_state = not controlled_everywhere
        self._set_controlled(pid, new_state)
        sender.IsChecked = new_state
        self._refresh_dots()

    def _set_all(self, controlled):
        for cb, pid in self.setting_rows:
            self._set_controlled(pid, controlled)
        self._fill_settings()
        self._refresh_dots()

    def _on_rename(self, sender, args):
        if self.loading:
            return
        sel = self._selected()
        if len(sel) != 1:
            return
        tid = sel[0]
        text = (self.name_box.Text or u"").strip()
        if text and text != self.by_id[tid].name:
            self.names[tid] = text
        else:
            self.names.pop(tid, None)
        self._refresh_dots(rename_tid=tid)

    def _refresh_dots(self, rename_tid=None):
        for item in self.lst.Items:
            g = item.Content
            g.Children[0].Fill = ORANGE if self._pending(item.Tag) else CLEAR
            if rename_tid is not None and item.Tag == rename_tid:
                g.Children[1].Text = self.names.get(item.Tag, self.by_id[item.Tag].name)
        n = len(set(self.names) | set(self.nonc))
        self.message.Text = (u"{} template(s) with changes not applied yet – click Accept".format(n)
                             if n else u"")

    # ---------- actions ----------
    def _need_clean(self):
        if self._has_pending():
            self.message.Text = u"Accept or discard your changes first."
            return False
        return True

    def _accept(self, sender, args):
        if not self._has_pending():
            self.message.Text = u"Nothing to apply."
            return
        n = len(set(self.names) | set(self.nonc))
        try:
            errors = tm.apply_pending(self.doc, dict(self.names), dict(self.nonc))
        except Exception as ex:
            errors = [str(ex)]
        keep = self._selected()
        self.names, self.nonc = {}, {}
        self._reload(keep)
        if errors:
            ui.show_result(u"View Template Manager", u"warning", u"Applied, with some problems",
                           stats=[(str(n), u"Templates")], details=errors[:6])
        else:
            self.message.Text = u""
            self.status.Text = u"Applied changes to {} template(s).   ".format(n) + self.status.Text

    def _discard(self, sender, args):
        self.names, self.nonc = {}, {}
        self._fill_list()
        self._refresh_dots()

    def _select_views(self, sender, args):
        t = self.by_id.get(sender.Tag)
        if t is None:
            return
        if not t.uses:
            self.message.Text = u"No view uses '{}'.".format(t.name)
            return
        self.uidoc.Selection.SetElementIds(List[ElementId](t.uses))
        self.message.Text = u"Selected the {} view(s) that use '{}' (see the Project Browser).".format(
            len(t.uses), t.name)

    def _copy(self, sender, args):
        sel = self._selected()
        if len(sel) != 1:
            self.message.Text = u"Select exactly one template to copy its settings."
            return
        self.clipboard = sel[0]
        self.message.Text = u"Copied the settings of '{}'. Select target templates and click Paste.".format(
            self.by_id[sel[0]].name)
        self._update_status()

    def _paste(self, sender, args):
        if not self._need_clean():
            return
        if self.clipboard not in self.by_id:
            self.message.Text = u"Copy the settings of a template first."
            return
        targets = [t for t in self._selected() if t != self.clipboard]
        if not targets:
            self.message.Text = u"Select the target template(s) - not the one you copied from."
            return
        src = self.by_id[self.clipboard]
        choice = ask_paste(self.doc, src, [self.by_id[t] for t in targets], self._settings(src.id), self.win)
        if not choice:
            return
        pids, flags = choice
        try:
            rep = tm.paste_settings(self.doc, src.id, targets, pids, flags)
        except Exception as ex:
            ui.alert(u"Paste settings", u"Nothing was changed. " + str(ex), u"error", u"Paste failed")
            return
        self._reload(targets)
        details = []
        if rep["not_in_target"]:
            details.append(u"{} setting(s) skipped: not available for the target view type.".format(rep["not_in_target"]))
        for tname, sname, err in rep["failed"][:5]:
            details.append(u"{} / {}: {}".format(tname, sname, err))
        status = u"success" if not rep["failed"] else u"warning"
        ui.show_result(u"Paste settings", status,
                       u"Settings pasted" if status == u"success" else u"Pasted, with some problems",
                       stats=[(str(rep["targets"]), u"Templates"), (str(rep["copied"]), u"Settings copied"),
                              (str(len(rep["failed"])), u"Failed")], details=details)

    def _duplicate(self, sender, args):
        if not self._need_clean():
            return
        sel = self._selected()
        if not sel:
            self.message.Text = u"Select the template(s) to duplicate."
            return
        try:
            new = tm.duplicate(self.doc, sel)
        except Exception as ex:
            self.message.Text = u"Duplicate failed: {}".format(ex)
            return
        self._reload(new)
        self.message.Text = u"Created {} copy(ies). Rename them on the right.".format(len(new))

    def _delete(self, sender, args):
        if not self._need_clean():
            return
        sel = self._selected()
        if not sel:
            self.message.Text = u"Select the template(s) to delete."
            return
        used = sum(len(self.by_id[t].uses) for t in sel)
        details = [u"• " + self.by_id[t].name for t in sel[:6]]
        if len(sel) > 6:
            details.append(u"... and {} more".format(len(sel) - 6))
        if used:
            details.append(u"{} view(s) use these templates and will have no template after this.".format(used))
        if not ui.confirm(u"Delete view templates", u"Delete {} template(s)?".format(len(sel)), details,
                          ok_text=u"Delete", status=u"error", owner=self.win):
            return
        try:
            tm.delete(self.doc, sel)
        except Exception as ex:
            self.message.Text = u"Delete failed: {}".format(ex)
            return
        if self.clipboard in sel:
            self.clipboard = None
        self._reload([])
        self.message.Text = u"Deleted {} template(s). Ctrl+Z in Revit brings them back.".format(len(sel))

    # ---------- window ----------
    def _drag(self, sender, args):
        try:
            self.win.DragMove()
        except Exception:
            pass

    def _close(self):
        if self._has_pending():
            if not ui.confirm(u"View Template Manager", u"Close without applying your changes?",
                              [u"{} template(s) have changes that are not applied.".format(
                                  len(set(self.names) | set(self.nonc)))],
                              ok_text=u"Close anyway", owner=self.win):
                return
        self.win.Close()

    def show(self):
        self.win.ShowDialog()


# =================================================================
# Paste settings dialog
# =================================================================
_PASTE = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        WindowStyle="None" AllowsTransparency="True" Background="Transparent" ResizeMode="NoResize"
        Width="640" Height="720" WindowStartupLocation="CenterOwner" ShowInTaskbar="False"
        FontFamily="Segoe UI" Title="Paste settings">
  <Window.Resources>__RES__</Window.Resources>
  <Border x:Name="Root" Margin="14" CornerRadius="22" Background="#0E1525" BorderBrush="#1F2A44" BorderThickness="1">
    <Border.Effect><DropShadowEffect BlurRadius="26" ShadowDepth="0" Opacity="0.6" Color="#000000"/></Border.Effect>
    <Grid Margin="26,20,26,20">
      <Grid.RowDefinitions>
        <RowDefinition Height="Auto"/><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/>
        <RowDefinition Height="*"/><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/>
      </Grid.RowDefinitions>
      <Grid x:Name="Header" Background="Transparent">
        <Grid.ColumnDefinitions><ColumnDefinition Width="*"/><ColumnDefinition Width="Auto"/></Grid.ColumnDefinitions>
        <StackPanel>
          <TextBlock Text="Paste settings" Foreground="White" FontSize="19" FontWeight="SemiBold"/>
          <TextBlock Text="Tick the settings to copy" Foreground="#7F8BA7" FontSize="12.5" Margin="0,3,0,0"/>
        </StackPanel>
        <Button x:Name="CloseBtn" Grid.Column="1" Style="{StaticResource Close}" VerticalAlignment="Top"/>
      </Grid>
      <Border Grid.Row="1" Margin="0,16,0,0" CornerRadius="16" Background="#151F35" Padding="18,12">
        <StackPanel>
          <Grid><Grid.ColumnDefinitions><ColumnDefinition Width="70"/><ColumnDefinition Width="*"/></Grid.ColumnDefinitions>
            <TextBlock Text="From" Foreground="#7F8BA7" FontSize="12.5"/>
            <TextBlock x:Name="SrcText" Grid.Column="1" Foreground="#E6EAF4" FontSize="12.5" FontWeight="SemiBold"
                       TextTrimming="CharacterEllipsis"/></Grid>
          <Grid Margin="0,8,0,0"><Grid.ColumnDefinitions><ColumnDefinition Width="70"/><ColumnDefinition Width="*"/></Grid.ColumnDefinitions>
            <TextBlock Text="To" Foreground="#7F8BA7" FontSize="12.5"/>
            <TextBlock x:Name="DstText" Grid.Column="1" Foreground="#E6EAF4" FontSize="12.5" FontWeight="SemiBold"
                       TextWrapping="Wrap"/></Grid>
          <TextBlock x:Name="Unsupported" Foreground="#F5A524" FontSize="12" Margin="0,8,0,0" TextWrapping="Wrap"/>
        </StackPanel>
      </Border>
      <Grid Grid.Row="2" Margin="0,14,0,10">
        <Grid.ColumnDefinitions><ColumnDefinition Width="*"/><ColumnDefinition Width="Auto"/><ColumnDefinition Width="Auto"/></Grid.ColumnDefinitions>
        __SEARCH_P__
        <CheckBox x:Name="OnlyControlled" Grid.Column="1" Style="{StaticResource Toggle}" Margin="12,0,4,0"
                  VerticalAlignment="Center"><TextBlock Text="Only controlled" Foreground="#C8D0E2"/></CheckBox>
        <StackPanel Grid.Column="2" Orientation="Horizontal" Margin="8,0,0,0" VerticalAlignment="Center">
          <Button x:Name="AllOn" Style="{StaticResource Chip}" Content="All"/>
          <Button x:Name="AllOff" Style="{StaticResource Chip}" Content="None" Margin="0"/>
        </StackPanel>
      </Grid>
      <Border Grid.Row="3" CornerRadius="16" Background="#151F35" Padding="16,8,8,8">
        <ScrollViewer VerticalScrollBarVisibility="Auto"><StackPanel x:Name="List" Margin="0,0,8,0"/></ScrollViewer>
      </Border>
      <CheckBox x:Name="Flags" Grid.Row="4" Style="{StaticResource Toggle}" Margin="2,14,0,0" IsChecked="True">
        <TextBlock Text="Also transfer the 'Controlled by template' flags" Foreground="#C8D0E2"/></CheckBox>
      <Grid Grid.Row="5" Margin="0,18,0,0">
        <Grid.ColumnDefinitions><ColumnDefinition Width="*"/><ColumnDefinition Width="Auto"/></Grid.ColumnDefinitions>
        <TextBlock x:Name="Count" Foreground="#7F8BA7" FontSize="12.5" VerticalAlignment="Center"/>
        <StackPanel Grid.Column="1" Orientation="Horizontal">
          <Button x:Name="CancelBtn" Style="{StaticResource Ghost}" Content="Cancel" Margin="0,0,10,0"/>
          <Button x:Name="OkBtn" Style="{StaticResource Primary}" Content="Paste" IsDefault="True"/>
        </StackPanel>
      </Grid>
    </Grid>
  </Border>
</Window>
"""


def ask_paste(doc, src_info, target_infos, src_settings, owner=None):
    """Returns (list of param ids, copy_flags) or None."""
    xaml = (_PASTE.replace(u"__RES__", _RES + _EXTRA)
                  .replace(u"__SEARCH_P__", _search(u"SearchP", u"Search setting")))
    win = XamlReader.Parse(xaml)
    if owner is not None:
        win.Owner = owner
    f = win.FindName
    f("SrcText").Text = src_info.name
    names = [t.name for t in target_infos]
    f("DstText").Text = u",  ".join(names[:4]) + (u"  (+{} more)".format(len(names) - 4) if len(names) > 4 else u"")

    target_ids = [set(tm.id_int(i) for i in t.view.GetTemplateParameterIds()) for t in target_infos]
    src_nonc = tm.non_controlled(src_info.view)
    unsupported = [pid for pid in src_settings if not all(pid in ids for ids in target_ids)]
    if unsupported:
        f("Unsupported").Text = (u"{} of {} settings are not available for the target view type and will be "
                                 u"skipped.".format(len(unsupported), len(src_settings)))
    # default: the settings the source controls (and that can be copied)
    chosen = set(pid for pid, s in src_settings.items()
                 if s.copyable and pid not in src_nonc and pid not in unsupported)
    rows = []
    state = {"result": None}

    def fill():
        panel = f("List")
        panel.Children.Clear()
        del rows[:]
        q = (f("SearchP").Text or u"").strip().lower()
        only = bool(f("OnlyControlled").IsChecked)
        groups = {}
        for pid, s in src_settings.items():
            if q and q not in s.name.lower():
                continue
            if only and pid in src_nonc:
                continue
            groups.setdefault(s.group, []).append(s)
        for gname in sorted(groups):
            head = _cell(gname.upper(), PURPLE, 11.5, bold=True)
            head.Margin = Thickness(2, 10, 0, 4)
            panel.Children.Add(head)
            for s in sorted(groups[gname], key=lambda x: x.name.lower()):
                ok = s.copyable and s.id not in unsupported
                row = Grid()
                row.Margin = Thickness(2, 3, 0, 3)
                _cols(row, [30, "*", "auto"])
                cb = CheckBox()
                cb.Style = win.FindResource("Check")
                cb.IsChecked = s.id in chosen
                cb.IsEnabled = ok
                cb.Tag = s.id
                cb.VerticalAlignment = VerticalAlignment.Center

                def toggled(sender, a):
                    if sender.IsChecked:
                        chosen.add(sender.Tag)
                    else:
                        chosen.discard(sender.Tag)
                    count()
                cb.Click += toggled
                row.Children.Add(cb)
                row.Children.Add(_cell(s.name, TEXT if ok else DIM, 13, 1))
                note = s.value
                if not s.copyable:
                    note = u"Revit API cannot copy this"
                elif s.id in unsupported:
                    note = u"not in target view type"
                elif s.id in src_nonc:
                    note = (note + u"   " if note else u"") + u"(not controlled)"
                v = _cell(note, MUTED if ok else DIM, 12, 2)
                v.MaxWidth = 240
                v.Margin = Thickness(12, 0, 0, 0)
                row.Children.Add(v)
                panel.Children.Add(row)
                if ok:
                    rows.append(cb)
        count()

    def count():
        f("Count").Text = u"{} setting(s) will be copied to {} template(s)".format(len(chosen), len(target_infos))

    def set_all(on):
        for cb in rows:
            if on:
                chosen.add(cb.Tag)
            else:
                chosen.discard(cb.Tag)
        fill()

    def ok(s, a):
        if not chosen:
            f("Count").Text = u"Tick at least one setting."
            return
        state["result"] = (sorted(chosen), bool(f("Flags").IsChecked))
        win.Close()

    _hook_search(f("SearchP"), f("SearchPHint"), fill)
    f("OnlyControlled").Click += lambda s, a: fill()
    f("AllOn").Click += lambda s, a: set_all(True)
    f("AllOff").Click += lambda s, a: set_all(False)
    f("OkBtn").Click += ok
    f("CancelBtn").Click += lambda s, a: win.Close()
    f("CloseBtn").Click += lambda s, a: win.Close()

    def drag(s, a):
        try:
            win.DragMove()
        except Exception:
            pass
    f("Header").MouseLeftButtonDown += drag
    fill()
    win.ShowDialog()
    return state["result"]
