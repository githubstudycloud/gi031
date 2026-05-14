# 05 · Frontend Integration —— Vue 3 与 React 18 对接

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

export interface ColumnSpec {
  code: string;
  label: string;
  data_type: string;
  group?: string;
  is_default_visible: boolean;
  default_order: number;
  default_width?: number;
  default_pinned?: 'none' | 'left' | 'right';
  sortable?: boolean;
  filterable?: boolean;
  display?: DisplaySpec;
}

export interface ReportConfig {
  meta: {
    report_type: string;
    name: string;
    version: number;
    summary_endpoint: string;
    detail_endpoint: string;
    user_pref_endpoint: string;
  };
  filters: FilterSpec[];
  tabs: { code: string; label: string; endpoint: string; sortable: boolean; paging: any }[];
  columns: Record<string, ColumnSpec[]>; // {summary: [...], detail: [...]}
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

## 5. `mergeColumns` 纯函数（共享）

```ts
export function mergeColumns(defaults: ColumnSpec[], prefs: UserColumnPref[]): RenderColumn[] {
  const prefMap = new Map(prefs.map(p => [p.field_code, p]));
  return defaults
    .map(c => {
      const p = prefMap.get(c.code);
      return {
        ...c,
        is_visible: p?.is_visible ?? c.is_default_visible,
        order:      p?.order_idx  ?? c.default_order,
        width:      p?.width      ?? c.default_width,
        pinned:     p?.pinned     ?? c.default_pinned ?? 'none',
      };
    })
    .sort((a, b) => a.order - b.order);
}
```

## 6. `buildQueryParams` 纯函数（共享）

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

## 7. 列定制 UX（两端共用）

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

## 8. 国际化与可访问性

- `field_def.label` 走 i18n key 的格式 `{report_type}.{code}`；前端 i18n 字典覆盖。
- 表格按 ARIA 规范实现键盘导航（方向键 / Home/End/PgUp/PgDn）。
- 下拉按 combobox 模式（aria-controls / aria-expanded / aria-activedescendant）。

## 9. 验收 checklist（用 chrome-devtools-mcp 跑）

- [ ] 打开 `/reports/daily_sales`，network 面板能看到 `/config` 一次、`/dropdowns/*` 按需多次、`/summary` 一次。
- [ ] 切区域 → product 下拉自动重拉，network 中 product 请求的 region_code = 选中的区域。
- [ ] 在区域下拉里 ⭐️ 上海 → 刷新页面 → 上海排在第一。
- [ ] 列配置勾掉"订单数" → 表格列消失；点"保存为我的默认" → 刷新仍消失。
- [ ] 修改 `field_def.is_hidden = true` → 重拉 config（version+1） → 该列从列选择器消失，但事实表数据未删。
