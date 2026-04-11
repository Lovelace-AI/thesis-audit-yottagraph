<template>
    <div class="d-flex flex-column fill-height">
        <div class="flex-grow-1 overflow-y-auto pa-4 pa-md-6">
            <!-- Idle: show thesis input -->
            <div
                v-if="status === 'idle'"
                class="d-flex align-center justify-center"
                style="min-height: 60vh"
            >
                <ThesisInput v-model="inputText" @submit="handleSubmit" />
            </div>

            <!-- Clarifying: agent is parsing thesis -->
            <div v-else-if="status === 'clarifying'">
                <ThesisBar :thesis="thesis" />
                <div class="content-area">
                    <ResearchProgress :steps="progress" />
                </div>
            </div>

            <!-- Awaiting Confirmation: show entity resolution -->
            <div v-else-if="status === 'awaiting_confirmation' && clarification">
                <ThesisBar :thesis="thesis" @edit="handleEdit" />
                <div class="content-area">
                    <EntityClarification
                        :clarification="clarification"
                        @confirm="handleConfirm"
                        @edit="handleEdit"
                    />
                </div>
            </div>

            <!-- Researching: show progress -->
            <div v-else-if="status === 'researching'">
                <ThesisBar :thesis="thesis" />
                <div class="content-area">
                    <ResearchProgress :steps="progress" />
                </div>
            </div>

            <!-- Done: show results -->
            <div v-else-if="status === 'done'">
                <ThesisBar :thesis="thesis" show-actions @edit="handleEdit" @reset="handleReset" />
                <div class="content-area">
                    <ResearchResults
                        v-if="results"
                        :results="results"
                        :steps="progress"
                        @edit="handleEdit"
                        @reset="handleReset"
                        @inspect="inspectEntity"
                    />
                    <v-card v-else-if="rawFallback" variant="outlined" class="raw-fallback">
                        <v-card-title class="raw-title">Agent Response</v-card-title>
                        <v-card-text class="raw-text">{{ rawFallback }}</v-card-text>
                    </v-card>
                </div>
            </div>

            <!-- Error -->
            <div v-else-if="status === 'error'">
                <ThesisBar :thesis="thesis" show-actions @edit="handleEdit" @reset="handleReset" />
                <div class="content-area">
                    <v-alert type="error" variant="tonal" class="mt-4">
                        {{ error || 'An unexpected error occurred.' }}
                    </v-alert>
                    <div class="d-flex justify-center mt-4">
                        <v-btn color="primary" @click="handleRetry">Retry</v-btn>
                    </div>
                </div>
            </div>

            <!-- Entity Info Card (anchored at bottom when a NEID is selected) -->
            <div v-if="inspectedNeid" class="content-area">
                <EntityInfoCard
                    :neid="inspectedNeid"
                    @close="inspectedNeid = ''"
                    @inspect="inspectEntity"
                />
            </div>
        </div>
    </div>
</template>

<script setup lang="ts">
    import type { EntitySelection } from '~/composables/useThesisResearch';
    import { useThesisResearch } from '~/composables/useThesisResearch';

    const {
        thesis,
        status,
        clarification,
        progress,
        results,
        rawFallback,
        error,
        submitThesis,
        confirmEntities,
        reset,
    } = useThesisResearch();

    const inputText = ref('');
    const inspectedNeid = ref('');

    function inspectEntity(neid: string) {
        inspectedNeid.value = neid;
        nextTick(() => {
            document
                .querySelector('.entity-info-card')
                ?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        });
    }

    function handleSubmit(thesisText: string) {
        inputText.value = thesisText;
        submitThesis(thesisText);
    }

    function handleConfirm(selections: EntitySelection[]) {
        confirmEntities(selections);
    }

    function handleEdit() {
        const currentThesis = thesis.value;
        inspectedNeid.value = '';
        reset();
        inputText.value = currentThesis;
    }

    function handleReset() {
        inspectedNeid.value = '';
        reset();
        inputText.value = '';
    }

    function handleRetry() {
        if (inputText.value) {
            submitThesis(inputText.value);
        }
    }
</script>

<script lang="ts">
    /**
     * Inline sub-component: thesis display bar shown in non-idle states.
     * Kept here because it's only used by this page.
     */
    export const ThesisBar = defineComponent({
        name: 'ThesisBar',
        props: {
            thesis: { type: String, required: true },
            showActions: { type: Boolean, default: false },
        },
        emits: ['edit', 'reset'],
        setup(props, { emit }) {
            return () =>
                h('div', { class: 'thesis-bar' }, [
                    h('div', { class: 'thesis-bar-content' }, [
                        h('span', { class: 'thesis-bar-label' }, 'Thesis:'),
                        h('span', { class: 'thesis-bar-text' }, props.thesis),
                    ]),
                    props.showActions
                        ? h('div', { class: 'thesis-bar-actions' }, [
                              h(
                                  resolveComponent('v-btn'),
                                  {
                                      variant: 'text',
                                      size: 'small',
                                      onClick: () => emit('edit'),
                                  },
                                  () => 'Edit & Re-run'
                              ),
                              h(
                                  resolveComponent('v-btn'),
                                  {
                                      variant: 'text',
                                      size: 'small',
                                      onClick: () => emit('reset'),
                                  },
                                  () => 'New Thesis'
                              ),
                          ])
                        : null,
                ]);
        },
    });
</script>

<style scoped>
    .content-area {
        max-width: 960px;
        margin: 0 auto;
        padding-top: 16px;
    }

    .thesis-bar {
        background: var(--lv-surface);
        border-radius: 8px;
        padding: 12px 16px;
        margin-bottom: 16px;
        display: flex;
        align-items: flex-start;
        gap: 12px;
        max-width: 960px;
        margin-left: auto;
        margin-right: auto;
    }

    .thesis-bar-content {
        flex: 1;
        min-width: 0;
    }

    .thesis-bar-label {
        font-family: var(--font-mono);
        font-size: 0.7rem;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: var(--lv-silver);
        display: block;
        margin-bottom: 4px;
    }

    .thesis-bar-text {
        font-size: 0.95rem;
        line-height: 1.4;
    }

    .thesis-bar-actions {
        flex-shrink: 0;
        display: flex;
        gap: 4px;
    }

    .raw-fallback {
        max-width: 720px;
        margin: 0 auto;
    }

    .raw-title {
        font-family: var(--font-headline);
        font-size: 1rem;
    }

    .raw-text {
        white-space: pre-wrap;
        font-size: 0.9rem;
        line-height: 1.6;
    }
</style>
