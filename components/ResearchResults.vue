<template>
    <div class="research-results">
        <div class="results-header">
            <h2 class="section-title">Research Report</h2>
            <div v-if="entityNames.length" class="entities-examined">
                <span class="entities-label">Entities examined:</span>
                <v-chip
                    v-for="entity in entityNames"
                    :key="entity.name"
                    size="small"
                    variant="tonal"
                    :class="{ 'entity-link': !!entity.neid }"
                    @click="entity.neid && $emit('inspect', entity.neid)"
                >
                    {{ entity.name }}
                </v-chip>
            </div>
        </div>

        <!-- Analysis columns -->
        <v-row>
            <v-col cols="12" md="6">
                <v-card variant="tonal" color="success" class="analysis-card">
                    <v-card-title class="analysis-title">
                        <v-icon size="small" class="mr-2">mdi-thumb-up-outline</v-icon>
                        Supporting Argument
                    </v-card-title>
                    <v-card-text class="analysis-text">
                        {{ report.supporting_argument || 'No supporting argument provided.' }}
                    </v-card-text>
                </v-card>
            </v-col>

            <v-col cols="12" md="6">
                <v-card variant="tonal" color="warning" class="analysis-card">
                    <v-card-title class="analysis-title">
                        <v-icon size="small" class="mr-2">mdi-thumb-down-outline</v-icon>
                        Contradicting Argument
                    </v-card-title>
                    <v-card-text class="analysis-text">
                        {{ report.contradicting_argument || 'No contradicting argument provided.' }}
                    </v-card-text>
                </v-card>
            </v-col>
        </v-row>

        <!-- Final analysis -->
        <v-card v-if="report.final_analysis" variant="outlined" class="final-analysis-card mt-4">
            <v-card-title class="final-analysis-title">
                <v-icon size="small" class="mr-2">mdi-scale-balance</v-icon>
                Final Analysis
            </v-card-title>
            <v-card-text class="analysis-text">
                {{ report.final_analysis }}
            </v-card-text>
        </v-card>

        <!-- Raw entity data (expandable) -->
        <v-expansion-panels
            v-if="Object.keys(report.entity_data).length"
            variant="accordion"
            class="mt-4"
        >
            <v-expansion-panel v-for="(data, entityName) in report.entity_data" :key="entityName">
                <v-expansion-panel-title class="entity-data-title">
                    <v-icon size="small" class="mr-2">mdi-database-outline</v-icon>
                    {{ entityName }}
                    <v-chip
                        v-if="data.neid"
                        size="x-small"
                        variant="outlined"
                        class="ml-2 neid-chip"
                        @click.stop="$emit('inspect', data.neid)"
                    >
                        {{ data.neid }}
                    </v-chip>
                    <v-chip size="x-small" variant="tonal" class="ml-2">
                        {{ entityDataSummary(data) }}
                    </v-chip>
                </v-expansion-panel-title>
                <v-expansion-panel-text>
                    <EntityDataSection
                        v-if="data.news?.length"
                        title="News"
                        icon="mdi-newspaper-variant-outline"
                        :items="data.news"
                        :columns="['title', 'date', 'sentiment', 'url']"
                    />
                    <EntityDataSection
                        v-if="data.stock_prices?.length"
                        title="Stock Prices"
                        icon="mdi-chart-line"
                        :items="data.stock_prices.slice(0, 20)"
                        :columns="['date', 'open', 'high', 'low', 'close', 'volume']"
                    />
                    <EntityDataSection
                        v-if="data.filings?.length"
                        title="Filings"
                        icon="mdi-file-document-outline"
                        :items="data.filings"
                        :columns="['form_type', 'date', 'description']"
                    />
                    <EntityDataSection
                        v-if="data.events?.length"
                        title="Events"
                        icon="mdi-calendar-star"
                        :items="data.events"
                        :columns="['category', 'date', 'description']"
                    />
                    <EntityDataSection
                        v-if="data.relationships?.length"
                        title="Relationships"
                        icon="mdi-graph-outline"
                        :items="data.relationships"
                        :columns="['name', 'neid']"
                        @inspect="(neid: string) => $emit('inspect', neid)"
                    />
                </v-expansion-panel-text>
            </v-expansion-panel>
        </v-expansion-panels>

        <!-- Macro data (expandable) -->
        <v-expansion-panels
            v-if="Object.keys(report.macro_data).length"
            variant="accordion"
            class="mt-4"
        >
            <v-expansion-panel v-for="(series, query) in report.macro_data" :key="query">
                <v-expansion-panel-title class="entity-data-title">
                    <v-icon size="small" class="mr-2">mdi-trending-up</v-icon>
                    Macro: {{ query }}
                    <v-chip size="x-small" variant="tonal" class="ml-2">
                        {{ Array.isArray(series) ? series.length : 0 }} series
                    </v-chip>
                </v-expansion-panel-title>
                <v-expansion-panel-text>
                    <pre class="raw-data">{{ JSON.stringify(series, null, 2) }}</pre>
                </v-expansion-panel-text>
            </v-expansion-panel>
        </v-expansion-panels>

        <!-- Show Your Work -->
        <v-expansion-panels v-if="stepsWithResponses.length" variant="accordion" class="mt-4">
            <v-expansion-panel>
                <v-expansion-panel-title class="provenance-title">
                    <v-icon size="small" class="mr-2">mdi-magnify-scan</v-icon>
                    Show Your Work
                    <v-chip size="x-small" variant="tonal" class="ml-2">
                        {{ stepsWithResponses.length }} tool calls
                    </v-chip>
                </v-expansion-panel-title>
                <v-expansion-panel-text>
                    <div v-for="step in stepsWithResponses" :key="step.id" class="provenance-step">
                        <div class="provenance-step-header">
                            <v-chip size="x-small" variant="tonal" color="primary">
                                {{ step.tool }}
                            </v-chip>
                            <span class="provenance-step-args">{{ formatArgs(step.args) }}</span>
                        </div>
                        <pre v-if="step.response" class="provenance-step-response">{{
                            step.response
                        }}</pre>
                    </div>
                </v-expansion-panel-text>
            </v-expansion-panel>
        </v-expansion-panels>

        <div class="results-actions">
            <v-btn variant="outlined" @click="$emit('edit')">
                <v-icon start>mdi-pencil-outline</v-icon>
                Edit Thesis
            </v-btn>
            <v-btn variant="outlined" @click="$emit('reset')">
                <v-icon start>mdi-plus</v-icon>
                Propose New Thesis
            </v-btn>
        </div>
    </div>
</template>

<script setup lang="ts">
    import type { ReportResult, ResearchStep } from '~/composables/useThesisResearch';

    const props = defineProps<{
        report: ReportResult;
        steps?: ResearchStep[];
    }>();

    defineEmits<{
        edit: [];
        reset: [];
        inspect: [neid: string];
    }>();

    const entityNames = computed(() => {
        const entities = props.report.query?.entities ?? [];
        return entities
            .filter((e) => e.status === 'resolved')
            .map((e) => ({
                name: e.name || e.mentioned_as,
                neid: e.neid,
            }));
    });

    const stepsWithResponses = computed(() => (props.steps || []).filter((s) => s.response));

    function entityDataSummary(data: any): string {
        const parts: string[] = [];
        if (data.news?.length) parts.push(`${data.news.length} news`);
        if (data.stock_prices?.length) parts.push(`${data.stock_prices.length} prices`);
        if (data.filings?.length) parts.push(`${data.filings.length} filings`);
        if (data.events?.length) parts.push(`${data.events.length} events`);
        if (data.relationships?.length) parts.push(`${data.relationships.length} relationships`);
        return parts.join(', ') || 'no data';
    }

    function formatArgs(args: Record<string, any>): string {
        const entries = Object.entries(args);
        if (entries.length === 0) return '';
        return entries.map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(', ');
    }
</script>

<script lang="ts">
    /**
     * Inline sub-component for rendering a section of entity data as a table.
     */
    export const EntityDataSection = defineComponent({
        name: 'EntityDataSection',
        props: {
            title: { type: String, required: true },
            icon: { type: String, required: true },
            items: { type: Array as () => any[], required: true },
            columns: { type: Array as () => string[], required: true },
        },
        emits: ['inspect'],
        setup(props, { emit }) {
            return () =>
                h('div', { class: 'entity-data-section' }, [
                    h('div', { class: 'entity-data-section-header' }, [
                        h(
                            resolveComponent('v-icon'),
                            { size: 'x-small', class: 'mr-1' },
                            () => props.icon
                        ),
                        h('span', props.title),
                        h(
                            resolveComponent('v-chip'),
                            { size: 'x-small', variant: 'tonal', class: 'ml-2' },
                            () => `${props.items.length}`
                        ),
                    ]),
                    h(
                        resolveComponent('v-table'),
                        { dense: true, class: 'entity-data-table' },
                        () => [
                            h('thead', [
                                h(
                                    'tr',
                                    props.columns.map((col) => h('th', { key: col }, col))
                                ),
                            ]),
                            h(
                                'tbody',
                                props.items.slice(0, 20).map((item, idx) =>
                                    h(
                                        'tr',
                                        { key: idx },
                                        props.columns.map((col) => {
                                            const val = item[col];
                                            if (col === 'neid' && val) {
                                                return h('td', { key: col }, [
                                                    h(
                                                        'span',
                                                        {
                                                            class: 'linked-neid',
                                                            onClick: () =>
                                                                emit('inspect', String(val)),
                                                        },
                                                        String(val)
                                                    ),
                                                ]);
                                            }
                                            if (col === 'url' && val) {
                                                return h('td', { key: col }, [
                                                    h(
                                                        'a',
                                                        {
                                                            href: val,
                                                            target: '_blank',
                                                            rel: 'noopener',
                                                            class: 'data-link',
                                                        },
                                                        'link'
                                                    ),
                                                ]);
                                            }
                                            return h(
                                                'td',
                                                { key: col },
                                                val != null ? String(val) : '—'
                                            );
                                        })
                                    )
                                )
                            ),
                        ]
                    ),
                ]);
        },
    });
</script>

<style scoped>
    .research-results {
        width: 100%;
    }

    .results-header {
        margin-bottom: 20px;
    }

    .section-title {
        font-family: var(--font-headline);
        font-weight: 400;
        font-size: 1.3rem;
        letter-spacing: 0.02em;
        margin-bottom: 8px;
    }

    .entities-examined {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
        align-items: center;
    }

    .entities-label {
        font-family: var(--font-mono);
        font-size: 0.7rem;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: var(--lv-silver);
    }

    .entity-link {
        cursor: pointer;
    }

    .entity-link:hover {
        border-color: rgb(var(--v-theme-primary));
    }

    .analysis-card {
        height: 100%;
    }

    .analysis-title {
        font-family: var(--font-headline);
        font-size: 0.95rem;
        padding-bottom: 0;
    }

    .analysis-text {
        font-size: 0.9rem;
        line-height: 1.7;
        white-space: pre-wrap;
    }

    .final-analysis-card {
        border-color: rgba(128, 128, 128, 0.3);
    }

    .final-analysis-title {
        font-family: var(--font-headline);
        font-size: 0.95rem;
        padding-bottom: 0;
    }

    .entity-data-title {
        font-family: var(--font-headline);
        font-size: 0.9rem;
    }

    .neid-chip {
        font-family: var(--font-mono);
        font-size: 0.6rem;
        cursor: pointer;
    }

    .neid-chip:hover {
        border-color: rgb(var(--v-theme-primary));
    }

    .entity-data-section {
        margin-bottom: 16px;
    }

    .entity-data-section-header {
        display: flex;
        align-items: center;
        gap: 4px;
        margin-bottom: 8px;
        font-family: var(--font-mono);
        font-size: 0.75rem;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: var(--lv-silver);
    }

    .entity-data-table {
        font-size: 0.75rem;
    }

    .entity-data-table th {
        font-family: var(--font-mono);
        font-size: 0.65rem;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: var(--lv-silver);
    }

    .entity-data-table td {
        font-family: var(--font-mono);
        font-size: 0.7rem;
        max-width: 300px;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }

    .linked-neid {
        color: rgb(var(--v-theme-primary));
        cursor: pointer;
        text-decoration: underline;
        text-decoration-style: dotted;
    }

    .linked-neid:hover {
        text-decoration-style: solid;
    }

    .data-link {
        color: rgb(var(--v-theme-primary));
        text-decoration: none;
    }

    .data-link:hover {
        text-decoration: underline;
    }

    .raw-data {
        font-family: var(--font-mono);
        font-size: 0.7rem;
        line-height: 1.4;
        color: var(--lv-silver);
        background: rgba(128, 128, 128, 0.05);
        border-radius: 4px;
        padding: 8px;
        white-space: pre-wrap;
        word-break: break-word;
        max-height: 300px;
        overflow-y: auto;
    }

    .provenance-title {
        font-family: var(--font-mono);
        font-size: 0.8rem;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }

    .provenance-step {
        padding: 8px 0;
        border-bottom: 1px solid rgba(128, 128, 128, 0.1);
    }

    .provenance-step:last-child {
        border-bottom: none;
    }

    .provenance-step-header {
        display: flex;
        align-items: center;
        gap: 8px;
        margin-bottom: 4px;
    }

    .provenance-step-args {
        font-family: var(--font-mono);
        font-size: 0.7rem;
        color: var(--lv-silver);
    }

    .provenance-step-response {
        font-family: var(--font-mono);
        font-size: 0.7rem;
        line-height: 1.4;
        color: var(--lv-silver);
        background: rgba(128, 128, 128, 0.05);
        border-radius: 4px;
        padding: 8px;
        margin-top: 4px;
        white-space: pre-wrap;
        word-break: break-word;
        max-height: 200px;
        overflow-y: auto;
    }

    .results-actions {
        display: flex;
        justify-content: center;
        gap: 12px;
        margin-top: 32px;
        padding-top: 24px;
        border-top: 1px solid rgba(128, 128, 128, 0.2);
    }
</style>
