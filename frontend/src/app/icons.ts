import { Component, input } from '@angular/core';
import { LucideDynamicIcon, LucideIcon, LucideActivity as Activity, LucideArrowDown as ArrowDown, LucideArrowRight as ArrowRight, LucideArrowUp as ArrowUp, LucideArrowUpRight as ArrowUpRight, LucideBookOpen as BookOpen, LucideCheck as Check, LucideChevronDown as ChevronDown, LucideChevronLeft as ChevronLeft, LucideChevronRight as ChevronRight, LucideCircleQuestionMark as CircleHelp, LucideClock3 as Clock3, LucideCopy as Copy, LucideDownload as Download, LucideEllipsis as Ellipsis, LucideFile as File, LucideFileText as FileText, LucideHistory as History, LucideHouse as House, LucideImagePlus as ImagePlus, LucideLayers as Layers3, LucideLayoutGrid as LayoutGrid, LucideMaximize as Maximize, LucideMinus as Minus, LucideMonitorPlay as MonitorPlay, LucidePanelLeft as PanelLeft, LucidePaperclip as Paperclip, LucidePlus as Plus, LucideRedo2 as Redo2, LucideSave as Save, LucideSearch as Search, LucideSettings2 as Settings2, LucideSparkles as Sparkles, LucideSquare as Square, LucideSquareDashed as SquareDashed, LucideSquareStack as SquareStack, LucideTrash2 as Trash2, LucideType as Type, LucideUndo2 as Undo2, LucideX as X, LucideZap as Zap } from '@lucide/angular';

export const icons = { Activity, ArrowDown, ArrowRight, ArrowUp, ArrowUpRight, BookOpen, Check, ChevronDown, ChevronLeft, ChevronRight, CircleHelp, Clock3, Copy, Download, Ellipsis, File, FileText, History, House, ImagePlus, Layers3, LayoutGrid, Maximize, Minus, MonitorPlay, PanelLeft, Paperclip, Plus, Redo2, Save, Search, Settings2, Sparkles, Square, SquareDashed, SquareStack, Trash2, Type, Undo2, X, Zap };

@Component({
  selector: 'lucide-icon',
  imports: [LucideDynamicIcon],
  template: '<svg [lucideIcon]="img()" [size]="size()"></svg>',
})
export class StudioIcon {
  img = input.required<LucideIcon>();
  size = input<number>(20);
}
