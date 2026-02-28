#!/usr/bin/env python3
"""
Package a skill into a distributable .skill file
"""

import os
import sys
import yaml
import zipfile
import re
from pathlib import Path

def validate_skill(skill_path):
    """Validate skill structure and content"""
    errors = []
    
    # Check if SKILL.md exists
    skill_md_path = os.path.join(skill_path, 'SKILL.md')
    if not os.path.exists(skill_md_path):
        errors.append("SKILL.md file is missing")
        return errors
    
    # Read and validate SKILL.md
    with open(skill_md_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Check for YAML frontmatter
    if not content.startswith('---'):
        errors.append("SKILL.md must start with YAML frontmatter")
        return errors
    
    # Extract frontmatter
    try:
        parts = content.split('---', 2)
        if len(parts) < 3:
            errors.append("Invalid YAML frontmatter format")
            return errors
        
        frontmatter = yaml.safe_load(parts[1])
        
        # Check required fields
        if 'name' not in frontmatter:
            errors.append("Missing 'name' field in frontmatter")
        
        if 'description' not in frontmatter:
            errors.append("Missing 'description' field in frontmatter")
        
        # Validate name format
        if 'name' in frontmatter:
            name = frontmatter['name']
            if not re.match(r'^[a-z0-9-]+$', name):
                errors.append("Skill name must contain only lowercase letters, digits, and hyphens")
            
            if len(name) > 64:
                errors.append("Skill name must be under 64 characters")
        
        # Validate description
        if 'description' in frontmatter:
            desc = frontmatter['description']
            if len(desc) < 50:
                errors.append("Description should be at least 50 characters for effective triggering")
    
    except yaml.YAMLError as e:
        errors.append(f"Invalid YAML frontmatter: {e}")
    
    return errors

def package_skill(skill_path, output_dir=None):
    """Package skill into .skill file"""
    
    # Validate first
    errors = validate_skill(skill_path)
    if errors:
        print("Validation failed:")
        for error in errors:
            print(f"  - {error}")
        return False
    
    # Get skill name from SKILL.md
    skill_md_path = os.path.join(skill_path, 'SKILL.md')
    with open(skill_md_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    frontmatter = yaml.safe_load(content.split('---', 2)[1])
    skill_name = frontmatter['name']
    
    # Set output directory
    if output_dir is None:
        output_dir = os.path.dirname(skill_path) or '.'
    
    os.makedirs(output_dir, exist_ok=True)
    
    # Create .skill file (zip with .skill extension)
    skill_file = os.path.join(output_dir, f"{skill_name}.skill")
    
    with zipfile.ZipFile(skill_file, 'w', zipfile.ZIP_DEFLATED) as zf:
        # Add all files in skill directory
        for root, dirs, files in os.walk(skill_path):
            for file in files:
                file_path = os.path.join(root, file)
                # Get relative path from skill directory
                arcname = os.path.relpath(file_path, skill_path)
                zf.write(file_path, arcname)
    
    print(f"Successfully packaged skill: {skill_file}")
    return True

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python package_skill.py <skill-directory> [output-directory]")
        sys.exit(1)
    
    skill_path = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else None
    
    if not os.path.exists(skill_path):
        print(f"Error: Skill directory '{skill_path}' does not exist")
        sys.exit(1)
    
    success = package_skill(skill_path, output_dir)
    sys.exit(0 if success else 1)