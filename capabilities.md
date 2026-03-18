# Agent Capabilities Documentation

This document outlines the capabilities (tools/functions) available to the agent. These capabilities allow the agent to interact with the file system, execute commands, communicate with the user, and perform various other tasks. This documentation is intended to help other agents or systems understand what is available and to build UI components that leverage these capabilities.

## File System Operations

### `list_files`
Lists all files and directories under the given directory (defaults to repo root). Directories in the output will have a trailing slash.
- **Parameters:**
  - `path` (string, optional): The directory path to list files from. Defaults to the root of the repo.

### `read_file`
Reads the content of the specified file in the repo. Returns an error if the file does not exist.
- **Parameters:**
  - `filepath` (string, required): The path of the file to read, relative to the repo root.

### `write_file`
Creates a new file or overwrites an existing file.
- **Parameters:**
  - `filepath` (string, required): The path of the file to create or overwrite.
  - `content` (string, required): The content to write to the file.

### `delete_file`
Deletes the specified file. Returns an error message if the file does not exist.
- **Parameters:**
  - `filepath` (string, required): The path of the file to delete.

### `rename_file`
Renames and/or moves files and directories. Returns an error message if `filepath` is missing, if `new_filepath` already exists, or if the target parent directory does not exist.
- **Parameters:**
  - `filepath` (string, required): The original path of the file or directory.
  - `new_filepath` (string, required): The new path for the file or directory.

### `replace_with_git_merge_diff`
Performs a targeted search-and-replace to modify an existing file. The format is a Git merge diff, meaning it needs a string argument with search and replace blocks.
- **Parameters:**
  - `filepath` (string, required): The path of the file to modify.
  - `merge_diff` (string, required): The diff to apply to the file.

## Execution & Environment

### `run_in_bash_session`
Runs the given bash command in the sandbox. Successive invocations use the same bash session, but all invocations run from the repository root directory. Useful for installing dependencies, compiling code, running tests, etc.
- **Parameters:**
  - `command` (string, required): The bash command to run.

## Workflow & Planning

### `set_plan`
Sets the current plan to solve the issue, formatted in Markdown. Used after initial exploration or if the plan needs updating.
- **Parameters:**
  - `plan` (string, required): The plan to solve the issue, in Markdown format.

### `plan_step_complete`
Marks the current plan step as complete. Must be called after verifying changes.
- **Parameters:**
  - `message` (string, required): A succinct, descriptive message explaining what actions were taken. User-visible.

### `submit`
Commits the current code with a title and description, requesting user approval to push to their branch.
- **Parameters:**
  - `branch_name` (string, required): The name of the branch to commit to.
  - `commit_message` (string, required): The commit message.
  - `title` (string, required): The title of the submission.
  - `description` (string, required): The description of the submission.

### `done`
Indicates that the subagent has completed its task.
- **Parameters:**
  - `summary` (string, required): A summary of what was accomplished or the final result.

## Communication & User Interaction

### `message_user`
Sends a statement to the user to respond to a question, provide feedback, or give an update. Set `continue_working` to true to continue actions, or false to wait for input.
- **Parameters:**
  - `message` (string, required): The message to send.
  - `continue_working` (boolean, required): Whether to continue working after sending the message.

### `request_user_input`
Asks the user a question or asks for input and waits for a response.
- **Parameters:**
  - `message` (string, required): The question or prompt for the user.

## Web & Media

### `google_search`
Online google search to retrieve the most up to date information. The result contains top urls with title and snippets.
- **Parameters:**
  - `query` (string, required): The query to search for.

### `view_text_website`
Fetches the content of a website as plain text. Useful for accessing documentation or external resources.
- **Parameters:**
  - `url` (string, required): The URL of the website to fetch.

### `view_image`
Loads an image from the provided URL, allowing the agent to view and analyze its contents.
- **Parameters:**
  - `url` (string, required): The URL of the image to view.

### `read_image_file`
Reads the image file at the given filepath into context.
- **Parameters:**
  - `filepath` (string, required): The path of the image file to read.

### `read_media_file`
Reads a media file (image or video) from the machine into context. Supports image formats and .webm video formats.
- **Parameters:**
  - `filepath` (string, required): The path of the media file to read.

## Source Control & Review

### `pre_commit_instructions`
Gets instructions on a list of pre-commit steps needed before submission.
- **Parameters:** None.

### `request_code_review`
Requests a code review for the current change.
- **Parameters:** None.

### `read_pr_comments`
Reads any pending pull request comments that the user has sent.
- **Parameters:** None.

### `reply_to_pr_comments`
Replies to comments. Input must be a JSON string representing a list of objects with "comment_id" and "reply" keys.
- **Parameters:**
  - `replies` (string, required): The replies to the comments.

## State & Memory Management

### `reset_all`
Resets the entire codebase to its original state. Use this to undo all changes and start over.
- **Parameters:** None.

### `restore_file`
Restores the given file to its original state.
- **Parameters:**
  - `filepath` (string, required): The path of the file to restore.

### `initiate_memory_recording`
Starts recording information that will be useful for future tasks.
- **Parameters:** None.

## Frontend Verification

### `frontend_verification_instructions`
Returns instructions on how to write a Playwright script to verify frontend web applications and generate screenshots.
- **Parameters:** None.

### `frontend_verification_complete`
Indicates that the frontend changes have been verified.
- **Parameters:**
  - `screenshot_path` (string, required): The path to the screenshot of the frontend changes.
  - `additional_media_paths` (array of strings, optional): Optional list of paths to additional media files.

### `start_live_preview_instructions`
Returns instructions on how to start a live preview server.
- **Parameters:** None.

## Misc

### `record_user_approval_for_plan`
Records the user's approval for the plan.
- **Parameters:** None.

### `call_hello_world_agent`
Calls the Hello World Agency agent with a message and returns its response. Used for testing integration.
- **Parameters:**
  - `message` (string, required): The message to send.

## Deprecated Tools
- `grep`: Use grep with `run_in_bash_session` instead.
- `create_file_with_block`: Use `write_file` instead.
- `overwrite_file_with_block`: Use `write_file` instead.
