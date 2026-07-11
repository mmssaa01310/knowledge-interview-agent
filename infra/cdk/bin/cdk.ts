#!/usr/bin/env node
import { App } from "aws-cdk-lib";
import { AiInterviewerStack } from "../lib/ai-interviewer-stack";

const app = new App();
new AiInterviewerStack(app, "AiInterviewerStack");
