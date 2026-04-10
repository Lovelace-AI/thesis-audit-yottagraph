/**
 * Utility to migrate preferences from old structure to new app-namespaced structure
 * This is for apps that were using Aether before app namespacing was introduced
 */

import { usePrefsStore } from '~/composables/usePrefsStore';
import { useUserState } from '~/composables/useUserState';

export async function migratePreferencesToAppNamespace() {
    const { userId } = useUserState();
    const config = useRuntimeConfig();
    const appId = config.public.appId || 'aether-default';

    if (!userId.value) {
        console.error('Cannot migrate preferences: No user ID found');
        return;
    }

    // Get the prefs store functions
    const { readDoc, deleteCollection, listDocuments, listCollections } = usePrefsStore();

    // Old paths that need migration
    const oldGeneralPath = `/users/${userId.value}/general/settings`;
    const newAppPath = `/users/${userId.value}/apps/${appId}/settings/general`;

    try {
        // Check if old preferences exist
        const oldSettings = await readDoc(oldGeneralPath);

        if (oldSettings && Object.keys(oldSettings).length > 0) {
            console.log('Found old preferences to migrate:', oldSettings);

            // Check if new preferences already exist
            const newSettings = await readDoc(newAppPath);

            if (newSettings && Object.keys(newSettings).length > 0) {
                console.warn(
                    'New preferences already exist. Skipping migration to prevent data loss.'
                );
                console.warn('Old settings:', oldSettings);
                console.warn('New settings:', newSettings);
                return;
            }

            // TODO: Implement actual migration
            // This would require adding a copyDoc method to PrefsStore
            console.log('Migration would copy from:', oldGeneralPath);
            console.log('To:', newAppPath);
            console.log('Then delete old preferences');

            // For now, just log what would happen
            console.warn(
                'Automatic migration not yet implemented. Please manually update your preference paths.'
            );
        } else {
            console.log('No old preferences found. Nothing to migrate.');
        }
    } catch (error) {
        console.error('Error during preference migration:', error);
    }
}

/**
 * Check if app is using the new namespaced preference structure
 */
export function isUsingNamespacedPreferences(): boolean {
    const config = useRuntimeConfig();
    return !!config.public.appId && config.public.appId !== 'aether-default';
}

/**
 * Get the current app's preference paths
 */
export function getAppPreferencePaths() {
    const { userId } = useUserState();
    const config = useRuntimeConfig();
    const appId = config.public.appId || 'aether-default';

    if (!userId.value) {
        throw new Error('No user ID available');
    }

    return {
        appPrefsRoot: `/users/${userId.value}/apps/${appId}`,
        appSettings: `/users/${userId.value}/apps/${appId}/settings/general`,
        globalPrefsRoot: `/users/${userId.value}/global`,
        globalSettings: `/users/${userId.value}/global/settings/general`,
        // Legacy paths for reference
        legacyGeneral: `/users/${userId.value}/general/settings`,
    };
}
