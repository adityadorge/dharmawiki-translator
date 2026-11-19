<?php
# ---------------------------------------------------------------------------
# Dharmawiki – settings for the Param machine-translation workflow.
# Append to LocalSettings.php (or require_once this file from it).
# Tested against MediaWiki 1.35.
# ---------------------------------------------------------------------------

# 1. A separate namespace for unreviewed machine translations (drafts).
#    Readers never see these unless they go looking; search engines are told not to index.
define( 'NS_TRANSLATION', 3000 );
define( 'NS_TRANSLATION_TALK', 3001 );
$wgExtraNamespaces[NS_TRANSLATION] = 'Translation';
$wgExtraNamespaces[NS_TRANSLATION_TALK] = 'Translation_talk';
$wgNamespacesWithSubpages[NS_TRANSLATION] = true;
$wgNamespaceRobotPolicies[NS_TRANSLATION] = 'noindex,nofollow';
# Leave drafts out of the default search.
$wgNamespacesToBeSearchedDefault[NS_TRANSLATION] = false;

# 2. Editors keep using the SAME editor (VisualEditor + source editor) in drafts.
$wgVisualEditorAvailableNamespaces['Translation'] = true;

# 3. Published translations live at "Page title/hi" – enable subpages in main namespace
#    so each translation shows a "< Page title" back-link to the original.
$wgNamespacesWithSubpages[NS_MAIN] = true;

# 4. Let the bot mark each translated page's content language (lang="hi", correct fonts,
#    hyphenation, and UniversalLanguageSelector behaviour).
$wgPageLanguageUseDB = true;

# 5. Bot account permissions.
#    Create the user "ParamTranslator", add it to the groups below, then create a
#    Bot Password at Special:BotPasswords with grants: "High-volume editing",
#    "Edit existing pages", "Create, edit, and move pages", "Change page language".
$wgGroupPermissions['bot']['pagelang'] = true;
$wgGroupPermissions['bot']['suppressredirect'] = true;
# Extension:Moderation is installed – the bot must bypass it, otherwise every draft
# waits in Special:Moderation. Drafts are already unpublished, so this is safe.
$wgGroupPermissions['automoderated']['skip-moderation'] = true;
# (add the bot user to the 'automoderated' and 'bot' groups via Special:UserRights)

# 6. Optional: only trusted reviewers may edit drafts.
# $wgNamespaceProtection[NS_TRANSLATION] = [ 'translation-review' ];
# $wgGroupPermissions['translator']['translation-review'] = true;
# $wgGroupPermissions['bot']['translation-review'] = true;
