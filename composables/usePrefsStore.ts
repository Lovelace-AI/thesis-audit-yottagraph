import { KVPrefsStore } from '~/utils/kvPrefsStore';

export type SettingsDoc = Record<string, string>;

/**
 * Reactive preference that auto-syncs to KV storage.
 *
 * @param docPath - KV document path, e.g. `/users/{userId}/apps/{appId}/settings/general`
 * @param fieldName - field name within the document
 * @param defaultValue - used when no stored value exists
 *
 * Usage:
 *   const pref = new Pref<boolean>(path, 'darkMode', true);
 *   await pref.initialize();
 *   pref.r.value;       // reactive ref (use in templates)
 *   pref.v;             // getter shorthand
 *   pref.set(false);    // persists to KV
 *
 * When KV is not configured (local dev), works with defaults but won't persist.
 * See the `pref` cursor rule for namespacing and architecture details.
 */
export class Pref<PrefType> {
    private _fieldName: string;
    private _docPath: string;
    private _defaultValue: PrefType;
    public r = ref<PrefType>();

    constructor(docPath: string, fieldName: string, defaultValue: PrefType) {
        this._fieldName = fieldName;
        this._docPath = docPath;
        this._defaultValue = defaultValue;
        this.r.value = defaultValue;
    }

    async changeSource(newDocPath: string, settingsDoc: SettingsDoc | undefined = undefined) {
        this._docPath = newDocPath;

        let value = undefined;
        if (settingsDoc) {
            if (settingsDoc.hasOwnProperty(this._fieldName)) {
                value = JSON.parse(settingsDoc[this._fieldName]) as PrefType;
            }
        } else {
            value = (await getPrefsStore().getValue<PrefType>(
                this._docPath,
                this._fieldName
            )) as PrefType;
        }

        if (value !== undefined) {
            this.r.value = value;
        } else {
            this.r.value = this._defaultValue;
        }
    }

    async initialize(settingsDoc: SettingsDoc | undefined = undefined) {
        console.log(`Initializing ${this._docPath}/${this._fieldName}`);
        if (settingsDoc) {
            let value = undefined;
            if (settingsDoc.hasOwnProperty(this._fieldName)) {
                value = JSON.parse(settingsDoc[this._fieldName]) as PrefType;
            }
            if (value !== undefined) {
                this.r.value = value;
            }
            console.log(
                `Initialized ${this._docPath}/${this._fieldName} to ${value} from settingsDoc`
            );
        } else {
            const value: PrefType | undefined = (await getPrefsStore().getValue<PrefType>(
                this._docPath,
                this._fieldName
            )) as PrefType;
            if (value !== undefined) {
                this.r.value = value;
            }
            console.log(
                `Initialized ${this._docPath}/${this._fieldName} to ${value} from prefsStore`
            );
        }

        // Only attach our watcher once we've read any stored value.
        watch(this.r, () => {
            if (this.r.value !== undefined) {
                this.set(this.r.value);
            }
        });
    }

    set(newValue: PrefType) {
        console.log(`Setting ${this._docPath}/${this._fieldName} to ${newValue}`);
        if (!this._docPath.startsWith('/users/')) {
            return;
        }

        getPrefsStore().setValue<PrefType>(this._docPath, this._fieldName, newValue);
        this.r.value = newValue;
    }

    get v(): PrefType | undefined {
        console.log(`Getting ${this._docPath}/${this._fieldName}: ${this.r.value}`);
        return this.r.value;
    }

    debugString(): string {
        return `Pref ${this._docPath}/${this._fieldName}: ${this.r.value}`;
    }
}

// #endregion

// #region Interface for the implementation classes.

export interface PrefsStore {
    // Document operations.
    copyDoc(from: string, to: string): Promise<void>;
    deleteDoc(path: string): Promise<void>;
    readDoc(path: string): Promise<SettingsDoc | undefined>;

    // Collection operations.
    copyCollection(from: string, to: string): Promise<void>;
    deleteCollection(path: string): Promise<void>;

    // Getters
    getValue<PrefType>(docPath: string, fieldName: string): Promise<PrefType | undefined>;

    // Listers
    listCollections(docPath: string): Promise<string[]>;
    listDocuments(collectionPath: string): Promise<string[]>;

    // Setters
    setValue<PrefType>(docPath: string, fieldName: string, value: PrefType): Promise<void>;
}

// #endregion

let userSettings: any = undefined;

const _kvAvailable = ref<boolean | null>(null);

let _prefsStore: PrefsStore | null = null;

function getPrefsStore(): PrefsStore {
    if (!_prefsStore) {
        _prefsStore = new KVPrefsStore();
    }
    return _prefsStore;
}

export async function initializePrefsStore() {
    await _initializePrefsStore();
}

async function saveUserInfo(
    userId: string,
    userName: string | undefined,
    userPicture: string | undefined
) {
    console.log(`Saving user info for ${userId}: ${userName} ${userPicture}`);

    await getPrefsStore().setValue<string>(
        `/userinfo/${userId}/`,
        'userName',
        userName ?? '[unknown]'
    );
    await getPrefsStore().setValue<string>(
        `/userinfo/${userId}/`,
        'userPicture',
        userPicture ?? ''
    );
}

async function _initializePrefsStore() {
    const { userId, userName, userPicture } = useUserState();
    const config = useRuntimeConfig();
    const appId = config.public.appId || 'aether-default';

    if (userId.value === undefined) {
        console.error(`ERROR: No user ID found; skipping prefs store initialization.`);
        return;
    }

    // Check KV availability and surface it as a reactive flag.
    try {
        const status = await $fetch<{ available: boolean }>('/api/kv/status');
        _kvAvailable.value = status.available;
        if (!status.available) {
            console.warn(
                '[Pref] KV not configured — preferences will use defaults and will not persist across refreshes. ' +
                    'Set KV_REST_API_URL and KV_REST_API_TOKEN for persistence.'
            );
        }
    } catch {
        _kvAvailable.value = false;
    }

    // App-specific preferences path with namespace
    const appPrefsPrefix = `/users/${userId.value}/apps/${appId}`;
    // Global preferences path for cross-app settings
    const globalPrefsPrefix = `/users/${userId.value}/global`;

    _prefsStore = getPrefsStore();

    await saveUserInfo(userId.value, userName.value, userPicture.value);
}

export function usePrefsStore() {
    async function deleteCollection(path: string) {
        await getPrefsStore().deleteCollection(path);
    }

    async function listCollections(path: string) {
        const list = await getPrefsStore().listCollections(path);
        return list;
    }

    async function listDocuments(collectionPath: string) {
        const list = await getPrefsStore().listDocuments(collectionPath);
        return list;
    }

    async function readDoc(path: string) {
        const doc = await getPrefsStore().readDoc(path);
        return doc;
    }

    return {
        userSettings,
        /** Whether KV storage is configured and available. null until checked. */
        kvAvailable: computed(() => _kvAvailable.value),
        deleteCollection,
        listCollections,
        listDocuments,
        readDoc,
    };
}
