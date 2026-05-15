# 05 · Frontend Integration —— Vue 3 与 React 18 对接

> **V3 实战回馈（`apps/ai-metrics` 已上线验证）**
>
> - **Vue 3 + React 18 同一份代码生成像素级一致的 UI**：两端共享 `isLeaf` / `depth` / `leaves` / `buildHeaderMatrix` / `visibleColumns` / `fmt` 5 个纯函数（inline 同款），框架差异只在 v-for/map 语法层。
> - **离线部署友好**：`vue.esm-browser.prod.js` / `react.production.min.js` / `react-dom.production.min.js` / `htm.module.js` 4 个 CDN 文件全部 vendor 到 `frontend/vendor/` (总 ~316 KB)，HTML 引用 `../vendor/x.js` 相对路径；后端 FastAPI mount `/vendor` 静态服务。
> - **同比/环比 UI**：每个数字单元格在 `compare_with !== "none"` 时尾巴附 `<span class="delta up|down|flat">▲/▼/≈ X%</span>`；颜色 success/danger/muted。从 `_delta_pct_<metric>` 字段读。
> - **下钻列要 `is_default_visible: false` 时也要在 header_tree 中声明**——否则前端不知道列存在；hide/show 走 column drawer/列选择器。
> - **`buildHeaderMatrix` 空 tree 容错**：`if (!tree?.length) return {rows:[], maxD:0}` —— 初始加载阶段 useMemo/computed 必踩。
> - **`/config` 协议升级 v2**：新增 `primary_view.row_dim_options` / `row_favorite` / `row_filter` / `compare` / `versions` 端点指针。前端按 `meta.version` 失效缓存。



## 1. 设计原则

- **前端是哑客户端**：除"展示规则"外，所有"调哪个 URL、怎么传参、怎么分页"都来自 `/config` 响应。
- 一个 `ReportPage` 通用组件，路由参数即 `report_type`，复用度 = 100%。
- 框架差异封装在最薄的一层；核心逻辑（`buildQueryParams`、`mergeColumns`）是纯函数，两端共享一份 TypeScript。

## 2. 共享 TypeScript 契约 (`libs/contract-ts`)

```ts
export type FilterKind = 'date_range' | 'flat_dropdown' | 'hierarchy_dropdown'
                       | 'search_dropdown' | 'multi_select' | 'text';

export interface SourceSpec {
  endpoint: string;
  method: 'GET' | 'POST';
  paging?: { enabled: boolean; page_size: number };
  sortable_by?: string[];
  default_sort?: { field: string; dir: 'asc' | 'desc' }[];
  supports_favorite?: boolean;
  supports_filter?: boolean;
  params_in: string[];
}

export interface FilterSpec {
  code: string;
  label: string;
  kind: FilterKind;
  required?: boolean;
  default?: unknown;
  param: Record<string, string>;          // 业务字段名 → 数据接口字段名
  depends_on?: string[];
  source?: SourceSpec;
}

export interface ColumnSpec {                     // 叶子节点
  code: string;
  label: string;
  data_type: string;
  is_default_visible: boolean;
  default_order: number;
  default_width?: number;
  default_pinned?: 'none' | 'left' | 'right';
  sortable?: boolean;
  row_filterable?: boolean;
  drilldown?: { ref: string };
  display?: DisplaySpec;
}
export interface GroupNode {                       // 非叶子节点
  code: string;
  label: string;
  children: HeaderNode[];
}
export type HeaderNode = ColumnSpec | GroupNode;
export function isLeaf(n: HeaderNode): n is ColumnSpec {
  return !(n as GroupNode).children;
}

export interface DrilldownSpec {
  title: string;
  endpoint: string;
  method: 'GET' | 'POST';
  param_mapping: Record<string, string>;     // "row.region_code" → "region_code"
  paging?: { enabled: boolean; default_page_size?: number };
  supports_row_favorite?: boolean;
  header_tree: HeaderNode[];
}

export interface VersionSpec {
  endpoint: string;
  param: string;
  default: 'latest' | number;
  policy: 'latest_per_day' | 'pinned';
}

export interface ReportConfig {
  meta: { report_type: string; name: string; version: number;
          user_pref_endpoint: string; };
  filters: FilterSpec[];
  primary_keys: string[];                          // 业务主键，用于 row favorite key 序列化
  version: VersionSpec;
  primary_view: {
    endpoint: string;
    sortable: boolean;
    paging: any;
    row_favorite?: { enabled: boolean; endpoint: string; sort_on_top: boolean };
  };
  columns: Record<string, { header_tree: HeaderNode[] }>;
  drilldowns: Record<string, DrilldownSpec>;
}

export interface UserColumnPref {
  field_code: string;
  is_visible: boolean;
  order_idx: number;
  width?: number;
  pinned?: 'none' | 'left' | 'right';
}

// 纯函数：合并后端默认列 + 个人偏好
export function mergeColumns(
  defaults: ColumnSpec[],
  prefs: UserColumnPref[],
): RenderColumn[] { ... }

// 纯函数：把当前 filter 值翻译成 query string
export function buildQueryParams(
  filters: FilterSpec[],
  values: Record<string, unknown>,
): URLSearchParams { ... }
```

## 3. Vue 3 示例（Composition API + @tanstack/vue-query）

文件：`frontend/vue-demo/src/pages/ReportPage.vue`

```vue
<script setup lang="ts">
import { ref, computed, watch } from 'vue';
import { useRoute } from 'vue-router';
import { useQuery, useMutation } from '@tanstack/vue-query';
import { http } from '@/lib/http';
import { mergeColumns, buildQueryParams } from '@gi031/contract-ts';
import FilterBar from '@/components/FilterBar.vue';
import ColumnConfigDrawer from '@/components/ColumnConfigDrawer.vue';
import DataTable from '@/components/DataTable.vue';

const route = useRoute();
const reportType = computed(() => route.params.type as string);

// 1. 拉 config
const configQ = useQuery({
  queryKey: ['report-config', reportType],
  queryFn: async () => (await http.get(`/api/reports/${reportType.value}/config`)).data.data,
  staleTime: 5 * 60_000,
});

// 2. 拉用户列偏好
const prefsQ = useQuery({
  queryKey: ['user-col-pref', reportType],
  enabled: computed(() => !!configQ.data.value),
  queryFn: async () => (await http.get(`/api/users/me/columns/${reportType.value}?view=summary`)).data.data,
});

// 3. 当前筛选值（受 FilterBar 控制）
const filterValues = ref<Record<string, unknown>>({});

// 4. 数据查询
const tableQ = useQuery({
  queryKey: ['report-data', reportType, filterValues, 'summary'],
  enabled: computed(() => !!configQ.data.value),
  queryFn: async () => {
    const cfg = configQ.data.value!;
    const params = buildQueryParams(cfg.filters, filterValues.value);
    return (await http.get(cfg.meta.summary_endpoint, { params })).data.data;
  },
});

const columns = computed(() =>
  configQ.data.value
    ? mergeColumns(configQ.data.value.columns.summary, prefsQ.data.value?.items ?? [])
    : []);

const saveColumnPref = useMutation({
  mutationFn: (items: any) =>
    http.put(`/api/users/me/columns/${reportType.value}`, { view: 'summary', items, is_personal_default: true }),
  onSuccess: () => prefsQ.refetch(),
});
</script>

<template>
  <section v-if="configQ.data.value">
    <header>
      <h1>{{ configQ.data.value.meta.name }}</h1>
      <ColumnConfigDrawer
        :defaults="configQ.data.value.columns.summary"
        :prefs="prefsQ.data.value?.items"
        @save="saveColumnPref.mutate"
      />
    </header>

    <FilterBar
      :filters="configQ.data.value.filters"
      v-model="filterValues"
    />

    <DataTable
      :columns="columns"
      :rows="tableQ.data.value?.items ?? []"
      :loading="tableQ.isPending.value"
      :pagination="tableQ.data.value"
    />
  </section>
</template>
```

`FilterBar.vue` 关键片段（处理层级 + 搜索 + 收藏 + 分页）：

```vue
<script setup lang="ts">
import type { FilterSpec } from '@gi031/contract-ts';
import HierarchyDropdown from './HierarchyDropdown.vue';
import SearchDropdown from './SearchDropdown.vue';
import DateRange from './DateRange.vue';
const props = defineProps<{ filters: FilterSpec[]; modelValue: Record<string, unknown> }>();
const emit  = defineEmits<{ 'update:modelValue': [v: Record<string, unknown>] }>();

function setValue(code: string, v: unknown) {
  emit('update:modelValue', { ...props.modelValue, [code]: v });
}

// 父字段变了 → 把所有依赖它的子字段清空
function cascadeReset(parentCode: string) {
  const next = { ...props.modelValue };
  props.filters
    .filter(f => f.depends_on?.includes(parentCode))
    .forEach(f => delete next[f.code]);
  emit('update:modelValue', next);
}
</script>
<template>
  <div class="filter-bar">
    <template v-for="f in filters" :key="f.code">
      <DateRange v-if="f.kind === 'date_range'"
                 :spec="f" :value="modelValue[f.code]"
                 @input="v => setValue(f.code, v)" />
      <HierarchyDropdown v-else-if="f.kind === 'hierarchy_dropdown'"
                         :spec="f"
                         :value="modelValue[f.code]"
                         :parent-values="modelValue"
                         @input="v => { setValue(f.code, v); cascadeReset(f.code); }" />
      <SearchDropdown v-else-if="f.kind === 'search_dropdown'"
                      :spec="f"
                      :value="modelValue[f.code]"
                      :parent-values="modelValue"
                      @input="v => setValue(f.code, v)" />
    </template>
  </div>
</template>
```

`HierarchyDropdown.vue`（关键：分页 + 排序 + 收藏 + 懒加载子级）：

```vue
<script setup lang="ts">
import { ref, computed, watch } from 'vue';
import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/vue-query';
import type { FilterSpec } from '@gi031/contract-ts';
import { http } from '@/lib/http';

const props = defineProps<{ spec: FilterSpec; value: any; parentValues: Record<string, unknown> }>();
const emit  = defineEmits<{ input: [v: any] }>();
const qc = useQueryClient();

const keyword = ref('');
const sort    = ref(props.spec.source!.default_sort ?? []);
const onlyFav = ref(false);
const expanded = ref<Record<string, boolean>>({});

function loadChildren(parent?: string) {
  return useInfiniteQuery({
    queryKey: ['dropdown', props.spec.code, parent, keyword.value, sort.value, onlyFav.value],
    initialPageParam: 1,
    queryFn: async ({ pageParam }) => {
      const params: Record<string, any> = {
        page: pageParam,
        page_size: props.spec.source!.paging?.page_size ?? 50,
        sort: sort.value.map(s => `${s.field}:${s.dir}`).join(','),
        only_favorites: onlyFav.value ? 1 : 0,
      };
      if (parent) params.parent = parent;
      if (keyword.value) params.q = keyword.value;
      // 依赖字段
      props.spec.depends_on?.forEach(dep => { params[dep] = props.parentValues[dep]; });
      const r = await http.get(props.spec.source!.endpoint, { params });
      return r.data.data;
    },
    getNextPageParam: last => last.has_more ? last.page + 1 : undefined,
  });
}

const rootQ = loadChildren();

const toggleFav = useMutation({
  mutationFn: ({ option_value, favorited }: any) =>
    http.put(`/api/users/me/favorites/${props.spec.source!.endpoint.split('/').pop()}`, { option_value, favorited }),
  onSuccess: () => qc.invalidateQueries({ queryKey: ['dropdown', props.spec.code] }),
});
</script>
```

## 4. React 18 示例（Hooks + @tanstack/react-query）

文件：`frontend/react-demo/src/pages/ReportPage.tsx`

```tsx
import { useParams } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useState, useMemo } from 'react';
import { http } from '@/lib/http';
import { mergeColumns, buildQueryParams } from '@gi031/contract-ts';
import FilterBar from '@/components/FilterBar';
import DataTable from '@/components/DataTable';
import ColumnConfigDrawer from '@/components/ColumnConfigDrawer';

export default function ReportPage() {
  const { type } = useParams<{ type: string }>();
  const qc = useQueryClient();

  const configQ = useQuery({
    queryKey: ['report-config', type],
    queryFn: async () => (await http.get(`/api/reports/${type}/config`)).data.data,
    staleTime: 5 * 60_000,
  });

  const prefsQ = useQuery({
    queryKey: ['user-col-pref', type],
    enabled: !!configQ.data,
    queryFn: async () => (await http.get(`/api/users/me/columns/${type}?view=summary`)).data.data,
  });

  const [filterValues, setFilterValues] = useState<Record<string, unknown>>({});

  const tableQ = useQuery({
    queryKey: ['report-data', type, filterValues, 'summary'],
    enabled: !!configQ.data,
    queryFn: async () => {
      const cfg = configQ.data!;
      const params = buildQueryParams(cfg.filters, filterValues);
      return (await http.get(cfg.meta.summary_endpoint, { params })).data.data;
    },
  });

  const columns = useMemo(
    () => (configQ.data
      ? mergeColumns(configQ.data.columns.summary, prefsQ.data?.items ?? [])
      : []),
    [configQ.data, prefsQ.data],
  );

  const saveColumnPref = useMutation({
    mutationFn: (items: any) =>
      http.put(`/api/users/me/columns/${type}`, { view: 'summary', items, is_personal_default: true }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['user-col-pref', type] }),
  });

  if (!configQ.data) return <div>Loading…</div>;

  return (
    <section>
      <header>
        <h1>{configQ.data.meta.name}</h1>
        <ColumnConfigDrawer
          defaults={configQ.data.columns.summary}
          prefs={prefsQ.data?.items}
          onSave={(items) => saveColumnPref.mutate(items)}
        />
      </header>
      <FilterBar
        filters={configQ.data.filters}
        value={filterValues}
        onChange={setFilterValues}
      />
      <DataTable
        columns={columns}
        rows={tableQ.data?.items ?? []}
        loading={tableQ.isPending}
        pagination={tableQ.data}
      />
    </section>
  );
}
```

`FilterBar.tsx` 框架结构与 Vue 一致，只是用 props + state 替代 v-model。
`HierarchyDropdown.tsx` 用 `useInfiniteQuery` 实现同样的分页 + 排序 + 收藏 + 懒加载。

## 5. `mergeColumns` + 表头树渲染（共享）

不再返回扁平排序后的列；现在返回**带可见性合并的树**，由 `renderHeaderTree` 渲染嵌套 `<thead>`。

```ts
export interface LeafState extends ColumnSpec {
  visible: boolean; order: number; width?: number;
  pinned: 'none' | 'left' | 'right';
}
export type MergedNode =
  | (LeafState & { kind: 'leaf' })
  | { kind: 'group'; code: string; label: string; children: MergedNode[] };

export function mergeColumns(tree: HeaderNode[], prefs: UserColumnPref[]): MergedNode[] {
  const prefMap = new Map(prefs.map(p => [p.field_code, p]));
  function walk(n: HeaderNode): MergedNode | null {
    if (isLeaf(n)) {
      const p = prefMap.get(n.code);
      return {
        ...n, kind: 'leaf',
        visible: p?.is_visible ?? n.is_default_visible,
        order:   p?.order_idx  ?? n.default_order,
        width:   p?.width      ?? n.default_width,
        pinned:  p?.pinned     ?? n.default_pinned ?? 'none',
      };
    }
    const children = n.children.map(walk).filter(Boolean) as MergedNode[];
    children.sort((a, b) =>
      (a.kind === 'leaf' ? a.order : 0) - (b.kind === 'leaf' ? b.order : 0));
    return { kind: 'group', code: n.code, label: n.label, children };
  }
  return tree.map(walk).filter(Boolean) as MergedNode[];
}

// 列树的"可见叶子总数"=最后一行 th 数；"最大深度"=表头行数
export function treeDepth(n: MergedNode): number {
  if (n.kind === 'leaf') return 1;
  return 1 + Math.max(...n.children.map(treeDepth));
}
export function visibleLeaves(n: MergedNode): LeafState[] {
  if (n.kind === 'leaf') return n.visible ? [n] : [];
  return n.children.flatMap(visibleLeaves);
}
```

渲染（Vue/React 等价；React JSX 示意）：

```tsx
function renderHeader(tree: MergedNode[]) {
  const maxDepth = Math.max(...tree.map(treeDepth));
  const rows: ReactNode[][] = Array.from({length: maxDepth}, () => []);

  function walk(n: MergedNode, depth: number) {
    if (n.kind === 'leaf') {
      if (!n.visible) return;
      rows[depth].push(
        <th key={n.code} rowSpan={maxDepth - depth} className={pinClass(n)}>
          {n.label}
          {n.sortable && <SortArrow column={n.code} />}
        </th>
      );
      return;
    }
    const span = n.children.flatMap(visibleLeaves).length;
    if (span === 0) return;
    rows[depth].push(
      <th key={n.code} colSpan={span} className="group">{n.label}</th>
    );
    n.children.forEach(c => walk(c, depth + 1));
  }
  tree.forEach(n => walk(n, 0));
  return <thead>{rows.map((r, i) => <tr key={i}>{r}</tr>)}</thead>;
}
```

**始终吸顶**：所有 thead `<tr>` 都加 `position: sticky; top: <累计高度>; z-index: 2`；最后一行 sticky 在 `top: <header rows 累计高度>`。CSS：

```css
.table-wrap { overflow: auto; max-height: 70vh; }
thead tr:nth-child(1) th { position: sticky; top: 0;  z-index: 3; }
thead tr:nth-child(2) th { position: sticky; top: 34px; z-index: 3; }
thead tr:nth-child(3) th { position: sticky; top: 68px; z-index: 3; }
/* 用 JS 测量 + 写 CSS var 更稳：--th-h-1, --th-h-2 ... */
```

## 6. 下钻交互（drilldown modal）

被点击单元格 → 从 `config.drilldowns[col.drilldown.ref]` 取 spec → 按 `param_mapping` 把 `{row, filter, cell}` 翻译成请求 body → POST 拿数据 → 模态框内复用同一份 header_tree 渲染逻辑。

```ts
async function openDrilldown(col: LeafState, row: any) {
  const ref = col.drilldown!.ref;
  const spec = config.drilldowns[ref];
  const body = applyParamMapping(spec.param_mapping, {
    row,
    filter: filterValues,
    cell:   { column: col.code, value: row[col.code] },
  });
  const r = await http({
    url: spec.endpoint, method: spec.method ?? 'POST',
    data: body, params: { page: 1, page_size: spec.paging?.default_page_size ?? 50 }
  });
  openModal({
    title: interpolate(spec.title, body),
    headerTree: mergeColumns(spec.header_tree, /* drill-modal 个人 prefs */ []),
    rows: r.data.data.items,
    pagination: r.data.data,
    onSort: (s) => ..., onPage: (p) => ...,
  });
}
```

模态框 UX 要点：
- 标题模板支持 `{row.x}` / `{filter.x}` / `{cell.value}` 占位符。
- 翻页/排序在模态框内独立，不污染主表状态。
- 关闭后回到主表，滚动位置保留。
- 弹框右上角加 "复制为 URL"（包含所有参数，便于分享排查链接）。

## 7. 数据行收藏与行级筛选

**行收藏**：每行第一列前一个空槽渲染 ⭐，已收藏=填充金色，未收藏=空心灰色（**对比度要拉满**，参考 §11 视觉指南）。

```tsx
<td className="favcell">
  <button
    className={"star " + (row._row_favorite ? 'on' : 'off')}
    aria-pressed={row._row_favorite}
    onClick={() => toggleRowFav(row)}
  >{row._row_favorite ? '★' : '☆'}</button>
</td>
```

收藏置顶通过 `sort` 参数处理：默认 sort 列表前面插一项 `_row_favorite:desc`。后端按 `user_row_favorite` JOIN 出 `_row_favorite` 列。

**列级筛选**：列右上角一个漏斗 icon，点开浮层填谓词。前端把所有列的谓词聚合为 `row_filter` JSON，URL-encode 后传给数据接口。

```
区域 ⏷  [漏斗]                       — 点漏斗弹出
  ┌─────────────────────────┐
  │ 操作:  [包含 ▾]          │
  │ 值:    [拿铁          ]  │
  │ □ 多选 (in / not_in)     │
  │ [清除] [应用]            │
  └─────────────────────────┘
```

被启用筛选的列头显示一个小蓝点提示。

## 8. 版本选择器

工具栏靠右一个 chip：

```
版本: [v3 (最新) ▾]   日期 2026-05-13
```

下拉展示该日期的所有版本（来自 `/api/reports/{type}/versions`）；日期范围下显示"每日最新"+按钮 [按日逐天选择]。

```tsx
<VersionPicker
  spec={config.version}
  date={filterValues.business_date}
  onChange={(v) => setVersion(v)}
/>
```

切换非最新版会有红色提示带："正在查看历史版本 v2 (2026-05-13 09:21 由 scheduler)"，避免业务方误读。

## 9. `buildQueryParams` 纯函数（共享）

```ts
export function buildQueryParams(filters: FilterSpec[], values: Record<string, unknown>) {
  const params = new URLSearchParams();
  for (const f of filters) {
    const v = values[f.code];
    if (v == null) continue;
    if (f.kind === 'date_range') {
      const { from, to } = v as any;
      if (from) params.set(f.param.from, from);
      if (to)   params.set(f.param.to,   to);
    } else if (Array.isArray(v)) {
      v.forEach(x => params.append(f.param.value, String(x)));
    } else {
      params.set(f.param.value, String(v));
    }
  }
  return params;
}
```

## 10. 列定制 UX（两端共用）

`ColumnConfigDrawer` 内部结构：

```
┌─────────────────────────────────────────────────────────┐
│ 列配置                                       [恢复默认]  │
├─────────────────────────────────────────────────────────┤
│  ▶ 维度        (4 项, 已选 4)                            │
│       ☑ 区域        [↑↓] [📌左]                          │
│       ☑ 产品        [↑↓]                                 │
│       ☑ 业务日期    [↑↓]                                 │
│  ▶ 度量        (8 项, 已选 5)                            │
│       ☑ GMV        [↑↓] [📌右]                           │
│       ☑ 订单数      [↑↓]                                 │
│       ☐ 退款金额    [↑↓]   (非默认列)                    │
│  ▶ 高级度量    (12 项, 已选 0)                           │
│       ☐ 安全库存天数 ...                                 │
├─────────────────────────────────────────────────────────┤
│ □ 保存为我的默认                       [取消]  [应用]    │
└─────────────────────────────────────────────────────────┘
```

操作语义：
- "应用"：本次会话生效；仅前端 state 更新。
- "保存为我的默认"勾选 + 应用：PUT `/api/users/me/columns/{type}` with `is_personal_default=true`。
- "恢复默认"：DELETE `/api/users/me/columns/{type}?view=summary`，再用 config.columns.summary 重渲染。

## 11. 视觉指南（应对"样式不太美观"）

| 项 | 规则 |
|---|---|
| 字体栈 | `-apple-system, BlinkMacSystemFont, "Segoe UI Variable", "PingFang SC", "Microsoft YaHei UI", sans-serif`；数值表格统一 `font-variant-numeric: tabular-nums` |
| 字号 | 表格 13px；表头 12px 加粗；toolbar 12px；H1 18px |
| 颜色 | 主色 `#2563eb`；强调（收藏）`#f59e0b` (on) / `#cbd5e1` (off)；行 hover `#eff6ff` |
| 间距 | 8 / 12 / 16 / 24 网格；表格单元格 `padding: 6px 12px` |
| 边框 | 表格用 1px `#e3e5ea`；表头组合并底边 2px `#c7d2fe`；活跃列底边 2px 主色 |
| 阴影 | 弹框 `0 16px 32px rgba(0,0,0,.12)`；面板 `0 1px 2px rgba(15,23,42,.05)` |
| 收藏星 | **★ 实心** + 金色 + 1px 描边 (`#d97706`)；**☆ 空心** + 灰 (`#cbd5e1`)；hover 时未收藏变金色虚线提示 |
| 表头吸顶 | 表头每行加 `box-shadow: 0 1px 0 #c9ccd3 inset`；最后一行加底部分割阴影，滚动时视觉延续 |
| 表格密度 | 提供三档：紧凑(28px) / 默认(32px) / 宽松(40px)；放在右上角 ⋮ 菜单 |
| 钉列阴影 | 左钉右侧加 `box-shadow: 1px 0 0 #e3e5ea`；右钉左侧加 `box-shadow: -1px 0 0 #e3e5ea`；横向滚动时显得有"层"  |
| 暗色 | 第二期再做；用 CSS variable + `:root.dark` |

## 12. 国际化与可访问性

- `field_def.label` 走 i18n key 的格式 `{report_type}.{code}`；前端 i18n 字典覆盖。
- 表格按 ARIA 规范实现键盘导航（方向键 / Home/End/PgUp/PgDn）。
- 下拉按 combobox 模式（aria-controls / aria-expanded / aria-activedescendant）。

## 13. 维度行树状渲染

第一个维度列（如 `region_code`）若启用"行树状"，渲染规则：

1. 行数据每条带 `_depth` / `_tree_key` / `_tree_parent` / `_is_aggregate` / `_has_children` / `_tree_label`。
2. 维护一个 `expandedTreeKeys: Set<string>`，默认根节点展开。
3. `visibleRows = rows.filter(r => 所有祖先都在 expandedTreeKeys 内)`
4. 第一列单元格：
   - `<span class="tree-indent" style="width: {depth*16}px"></span>`
   - `<span class="tree-caret">▾ / ▸ / · </span>` （有子且展开/有子未展开/叶子）
   - `<span>{_tree_label}</span>`
5. 聚合行整行加 class `agg-d{depth}`，背景渐深、字体加粗；不显示收藏 ★ 和审计徽章；下钻禁用。
6. 度量列：聚合行用 `_is_aggregate=true` 时仍按 `display` 格式化，但服务端已算好聚合值；客户端模式则前端 sum/avg。

```ts
// Vue 3
const visible = computed(() => {
  if (!treeMode.value) return rows.value;
  const expanded = expandedKeys.value;
  return rows.value.filter(r => {
    let cur = r._tree_parent;
    while (cur != null) {
      if (!expanded.has(cur)) return false;
      cur = rowMap.value.get(cur)?._tree_parent ?? null;
    }
    return true;
  });
});
```

React 等价写法用 `useMemo`。两端共享 `visibleTreeRows()` 纯函数。

## 14. 分页模式 (服务端 / 客户端 / 关闭)

| 模式 | 状态 | 翻页/排序时 |
|---|---|---|
| `server` | 后端切片；前端只缓存当前页 | 每次都 refetch |
| `client` | 后端一次返全量；前端 `state.allRows` 缓存，`state.rows` 是切片 | 不 refetch，本地切片 |
| `none`   | 全量直接展示 | 不切片，不翻页 |

```ts
async function refresh() {
  const params = buildFilterParams();
  if (pagingMode === 'server') {
    params.page = state.page; params.page_size = state.page_size;
  } else {
    params.paging = 'none';
  }
  const r = await http.get(endpoint, { params });
  state.allRows = r.data.data.items;
  if (pagingMode === 'client') {
    state.rows = r.data.data.items.slice((state.page-1)*state.page_size, state.page*state.page_size);
  } else {
    state.rows = r.data.data.items;
  }
}
```

切换模式不重置筛选/排序；切到 `client` 后翻页只动 `state.rows` 切片不发请求。

## 15. 视图模板 (前端内置 + 后端动态)

### 15.1 接入策略

```ts
const BUILTIN: ViewTemplate[] = [
  { code:'default', name:'默认',     source:'frontend_builtin', config:{...} },
  { code:'compact', name:'紧凑',     source:'frontend_builtin', config:{ density:'compact', page_size:100 } },
  { code:'kanban',  name:'看板',     source:'frontend_builtin', config:{ show_kpi:true, kpi:[...] } },
  { code:'tree',    name:'维度树状', source:'frontend_builtin', config:{ tree_mode:true, paging_mode:'client', page_size:200 } }
];

const remoteTemplates = useQuery(['view-templates', type],
  () => http.get(`/api/view_templates/${type}`).then(r => r.data.data.items));

const allTemplates = computed(() => [...BUILTIN, ...(remoteTemplates.data ?? [])]);
```

### 15.2 应用模板 (一个函数搞定)

```ts
async function applyTemplate(t: ViewTemplate) {
  state.density     = t.config.density     ?? 'normal';
  state.pagingMode  = t.config.paging_mode ?? 'server';
  state.page_size   = t.config.page_size   ?? 50;
  state.treeMode    = !!t.config.tree_mode;
  state.showKpi     = !!t.config.show_kpi;
  state.kpiDefs     = t.config.kpi ?? null;

  if (t.config.only_columns) {
    // 临时覆盖个人列偏好（不入库）
    state.tempColumnOverride = t.config.only_columns.map((code, idx) => ({
      field_code: code, is_visible: true, order_idx: idx + 1
    }));
  } else {
    state.tempColumnOverride = null;
  }
  if (t.config.default_sort) state.sort = { ...t.config.default_sort };

  await refresh();
}
```

### 15.3 Vue 3 与 React 18 都能干？是的

整套机制完全是**框架无关的 JSON-driven UI**：
- 服务端是普通 REST，返回 JSON。
- 客户端只需要做三件事——`fetch config + templates`、按 `header_tree` 渲染嵌套 `<thead>`、按 `kind` 渲染过滤器组件。
- 这些都是基本的 DOM 操作 + 状态管理，**Vue 的 Composition API + Pinia** 和 **React 的 hooks + Zustand/Redux** 都能 1:1 实现。

两端复用的纯函数（共享 TS 库 `@gi031/contract-ts`）：
- `mergeColumns(headerTree, prefs) → MergedNode[]`
- `buildHeaderMatrix(tree) → th 二维数组`
- `visibleTreeRows(rows, expanded) → 可见叶子+聚合`
- `buildQueryParams(filters, values, pagingMode) → URLSearchParams`
- `applyParamMapping(mapping, ctx) → 下钻请求 body`

框架差异只在**渲染语法**和**状态管理库**上；业务逻辑 0 重复。建议两端用同一组 `*.spec.ts` 单测覆盖纯函数。

## 16. 验收 checklist（用 chrome-devtools-mcp 跑）

- [ ] 打开 `/reports/daily_sales`，network 面板能看到 `/config` 一次、`/versions` 一次、`/summary` 一次。
- [ ] 表头展示 3 层结构（销售 > GMV 分渠道 > 线上/线下 > APP/Web 等）；滚动表格时所有表头行都保持吸顶。
- [ ] 切区域 → product 下拉自动重拉，network 中 product 请求的 region_code = 选中的区域。
- [ ] 在区域下拉里 ⭐ 上海 → 刷新页面 → 上海排在第一；星标对比明显（金色实心 vs 灰色空心）。
- [ ] 列配置勾掉"订单数" → 表格列消失；点"保存为我的默认" → 刷新仍消失。
- [ ] 单元格 `APP GMV` 点击 → 模态框打开，标题包含当前区域 / 产品 / 业务日期，列由 drilldown.header_tree 渲染。
- [ ] 列头漏斗 → 输入 `gmv_app >= 5000` → 仅符合行显示；列头出现蓝点。
- [ ] 数据行 ⭐ → 该行置顶；刷新后仍置顶。
- [ ] 版本选择器：当天有 v3/v2/v1；默认 v3；切到 v2 后页面顶部出现红色"历史版本"横幅。
- [ ] 修改 `field_def.is_hidden = true` → 重拉 config（version+1） → 该列从列选择器消失，但事实表数据未删。
- [ ] **树状行**：点 🌲 行树状 → 第一列出现 ▾ caret 与缩进；展开"全国 → 华东 → 上海" 看到 P001-P008 产品行；聚合行字体加粗、有背景色、无 ⭐ 与审计徽章。
- [ ] **分页模式**：切到"客户端 (后端返全量)" → network 看到 `paging=none` 请求；本地翻页不再发请求；切回服务端 → 翻页恢复带 `page/page_size`。
- [ ] **列值去重筛选**：点表头 ▽ → "列值挑选" tab → 拉到去重值列表（带 ×N 出现次数）+ 全选/反选 → 多勾几个 → 应用 → 行数减少。
- [ ] **视图模板**：点 🎨 切换"看板 (KPI)" → 顶部出现 4 张 KPI 卡片；切"财务总监周报"（后端模板）→ 列减到 6 列、自动开树状、page_size=200、默认按退款排序。
- [ ] 切回"默认"内置模板 → KPI 消失、列恢复、树状关闭。
